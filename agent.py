"""
Ocean SF Daily Lead Agent
--------------------------
An autonomous agent that researches one new sales/partnership lead per
day for Ocean SF (sustainable sailing apparel, San Francisco), drafts a
personalized outreach email for it, and saves that draft for a human to
review and send.

IMPORTANT — by design, this agent NEVER sends email automatically. It
only writes drafts to the local drafts/ folder. A human (you) reads each
draft, edits it if needed, and sends it yourself from your own email
client. This is intentional: unreviewed AI-drafted cold outreach to real
press contacts, boutiques, or influencers is a real reputational risk to
the brand, so a human stays in the loop before anything goes out.

Architecture:
  1. Picks today's lead category (press / influencer / boutique) on a
     rotating schedule, based on the calendar date.
  2. Loads a local JSON log of every lead already surfaced, so the agent
     never repeats itself.
  3. Calls the Claude API with the web_search tool enabled, instructing
     the model to research a real, current, specific lead and return it
     as structured JSON (not free text) — this is what makes the output
     reliably parseable and deliverable.
  4. Generates a full personalized outreach email (subject + body) for
     that lead.
  5. Validates + saves the new lead to the log.
  6. Writes the draft to drafts/YYYY-MM-DD_name.txt for you to review.

Run manually:
    python agent.py

Run on a schedule:
    See schedule_cron_example.txt or .github/workflows/daily_lead.yml
"""

import os
import json
import datetime
import re
from pathlib import Path

import anthropic

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
LOG_PATH = BASE_DIR / "leads_log.json"
DRAFTS_DIR = BASE_DIR / "drafts"

LEAD_TYPES = ["press", "influencer", "boutique"]

MODEL = "claude-sonnet-4-6"

BRAND_CONTEXT = """
Ocean SF is a woman-owned San Francisco sailing apparel brand founded by
Sydney Chaney-Thomas. It makes organic cotton midlayers and wardrobe
essentials as a sustainable alternative to synthetic polyester outdoor
apparel that sheds microplastics into the ocean.

Every piece is made to order, in small batches, in partnership with
sustainable production partners (not framed as "locally sewn").

Brand pillars: zero microfibers, made-to-order / small-batch production,
maritime heritage, ethical sustainable manufacturing, "Wear Your Values."

Core audiences:
- Competitive & leisure sailors, yacht club members (e.g. St. Francis Yacht
  Club, Richmond Yacht Club, Rolex Big Boat Series)
- Eco-minded luxury consumers who invest in durable, sustainable staples
- Coastal boutiques and specialty/sustainable concept stores
- Sailing clubs, academies, and maritime institutions

Voice: intentional, sophisticated, understated, maritime-grounded. Avoids
hype and trendy slang. Never uses em dashes ( — ) anywhere in outreach
copy — use commas or periods instead.
""".strip()

CAMPAIGN_CONTEXT = """
Ocean SF has an upcoming holiday season launch: new organic cotton
midlayers and wardrobe basics. This outreach should function as a FIRST
TOUCH, not a hard sell, the goal is to introduce Ocean SF, build a warm
relationship with this contact, and preview the upcoming launch, so that
a follow-up email once the line actually ships lands with someone who
already knows who we are and is expecting to hear from us again.
""".strip()

# Each lead type gets its own ask level and email instructions, since
# the right amount of "ask" differs a lot by audience:
#   - press/blog: soft ask, gauge interest in a future feature
#   - boutique:   no ask at all, pure warm introduction
#   - influencer: soft invite, no hard ask
EMAIL_INSTRUCTIONS = {
    "press": (
        "This email DOES include a soft ask: say Ocean SF would love to "
        "get in contact if the writer/outlet is interested in the "
        "upcoming holiday launch as a potential feature or story. Do "
        "NOT ask for a call or a meeting, just express openness to "
        "connect if there's interest. Offer to send photos or founder "
        "background if helpful."
    ),
    "boutique": (
        "This email has NO ask at all. It is a pure warm introduction "
        "that previews the upcoming holiday launch and says a follow-up "
        "will come once it's live. Do not request a wholesale "
        "conversation, line sheet, or any commitment in this email."
    ),
    "influencer": (
        "This email has a soft, casual invite only, not a hard ask. "
        "Mention the upcoming holiday launch feels like a fit for their "
        "content/audience, and say you'll follow up once it's live in "
        "case they'd want to feature it. No formal collaboration ask "
        "yet."
    ),
}

LEAD_TYPE_INSTRUCTIONS = {
    "press": (
        "Find a real, currently active newspaper, local news outlet, or "
        "blog (SF/Bay Area local news, or maritime / eco-lifestyle / "
        "sustainable fashion trade press) that covers brand stories, "
        "founder profiles, or sustainability angles. It must be a real, "
        "specific publication with a real URL you found via search — not "
        "a category placeholder."
    ),
    "influencer": (
        "Find a real, currently active SF Bay Area micro-influencer "
        "(roughly 5,000-50,000 followers) in sailing, sustainable "
        "fashion, or coastal/outdoor lifestyle whose audience plausibly "
        "overlaps with Ocean SF's. Must be a real account you found via "
        "search, with a real handle/link — not a hypothetical persona."
    ),
    "boutique": (
        "Find a real, currently operating independent boutique, concept "
        "store, or yacht club pro shop (San Francisco, Marin, or greater "
        "Bay Area) that sells apparel, home goods, or gifts in a style "
        "compatible with Ocean SF, and could plausibly stock it as a "
        "wholesale partner. Must be a real business you found via search, "
        "with a real name and location."
    ),
}

OUTPUT_SCHEMA_INSTRUCTIONS = """
After researching, respond with ONLY a single JSON object (no markdown
fences, no commentary before or after) matching this exact schema:

{
  "lead_type": "press" | "influencer" | "boutique",
  "name": "string - name of the outlet/person/business",
  "link": "string - real URL or handle",
  "location": "string",
  "why_fit": "string - 2-3 sentences on why this is a specific fit for "
             "Ocean SF, tied to something real and specific about them "
             "(not generic boilerplate)",
  "recommended_angle": "string - press pitch | influencer collab | "
             "wholesale stocking | internship/community spotlight, with "
             "one sentence of reasoning",
  "email_subject": "string - a specific, non-generic subject line for "
             "the outreach email. Should NOT sound like a sales pitch, "
             "this is a warm introduction, not an ask. No em dashes.",
  "email_body": "string - a full, ready-to-review outreach email, "
             "3-4 short paragraphs, in Ocean SF's voice (see brand "
             "context above: intentional, sophisticated, understated, "
             "maritime-grounded, no hype/slang, NO EM DASHES anywhere, "
             "use commas or periods instead). Reference something "
             "specific and real about this recipient. Follow the "
             "campaign context and this lead type's specific email "
             "instructions exactly. End with a warm sign-off. Sign off "
             "as 'Kyla, Ocean SF Team' and leave a placeholder "
             "[YOUR CONTACT INFO] at the very end for the sender to "
             "fill in. Never mention merino wool, petrochemicals, or "
             "'sewn locally', use 'polyester' and 'sustainable "
             "production partners' instead."
}
""".strip()


# ---------------------------------------------------------------------------
# Log management (this is the agent's "memory" of past leads)
# ---------------------------------------------------------------------------

def load_past_leads() -> list[dict]:
    if LOG_PATH.exists():
        return json.loads(LOG_PATH.read_text())
    return []


def save_lead(lead: dict) -> None:
    leads = load_past_leads()
    lead["date"] = datetime.date.today().isoformat()
    leads.append(lead)
    LOG_PATH.write_text(json.dumps(leads, indent=2))


def todays_lead_type() -> str:
    # Rotate deterministically through the three categories by date,
    # so "what type of lead is it today" is reproducible, not random.
    day_index = datetime.date.today().toordinal()
    return LEAD_TYPES[day_index % len(LEAD_TYPES)]


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def build_prompt(lead_type: str, past_leads: list[dict]) -> str:
    already_used = [
        l["name"] for l in past_leads if l.get("lead_type") == lead_type
    ]
    avoid_clause = (
        f"Do NOT suggest any of these — already contacted: {', '.join(already_used)}."
        if already_used
        else "No prior leads of this type yet."
    )

    return f"""
You are sourcing a B2B / partnership / press lead for the brand described
below. Use web search to find one REAL, CURRENT, SPECIFIC lead — not a
generic example.

BRAND CONTEXT:
{BRAND_CONTEXT}

CAMPAIGN CONTEXT:
{CAMPAIGN_CONTEXT}

TODAY'S LEAD CATEGORY: {lead_type}
{LEAD_TYPE_INSTRUCTIONS[lead_type]}

EMAIL INSTRUCTIONS FOR THIS CATEGORY:
{EMAIL_INSTRUCTIONS[lead_type]}

{avoid_clause}

{OUTPUT_SCHEMA_INSTRUCTIONS}
""".strip()


# ---------------------------------------------------------------------------
# Agent core: call the model with tool use, parse structured output
# ---------------------------------------------------------------------------

def extract_json(text: str) -> dict:
    """Pull the JSON object out of the model's final text response."""
    # Model is instructed to return raw JSON, but strip code fences just in case.
    cleaned = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in model output:\n{text}")
    return json.loads(match.group(0))


def run_agent() -> dict:
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from environment

    lead_type = todays_lead_type()
    past_leads = load_past_leads()
    prompt = build_prompt(lead_type, past_leads)

    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}],
    )

    # A tool-using response contains a mix of block types (text,
    # server_tool_use, web_search_tool_result). We only need the final
    # text block(s) — the model's synthesized answer after searching.
    text_blocks = [block.text for block in response.content if block.type == "text"]
    full_text = "\n".join(text_blocks)

    lead = extract_json(full_text)
    return lead


# ---------------------------------------------------------------------------
# Delivery — writes a DRAFT for a human to review. This agent never sends
# email on its own; that is a deliberate safety choice, not a limitation.
# ---------------------------------------------------------------------------

def deliver_lead(lead: dict) -> Path:
    DRAFTS_DIR.mkdir(exist_ok=True)

    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", lead.get("name", "lead")).strip("_")
    filename = f"{datetime.date.today().isoformat()}_{lead.get('lead_type','lead')}_{safe_name}.txt"
    draft_path = DRAFTS_DIR / filename

    draft_text = f"""OCEAN SF — DRAFT OUTREACH ({lead.get('lead_type', '?').upper()})
Generated: {datetime.date.today().isoformat()}
STATUS: DRAFT — review before sending. This was NOT sent automatically.

Lead: {lead.get('name')}
Link: {lead.get('link')}
Location: {lead.get('location')}

Why this is a fit:
{lead.get('why_fit')}

Recommended angle:
{lead.get('recommended_angle')}

-----------------------------------------------------------
SUBJECT: {lead.get('email_subject')}
-----------------------------------------------------------

{lead.get('email_body')}

-----------------------------------------------------------
Reminder: verify the recipient's actual email address before sending —
this agent did not look up or confirm a contact email for you.
"""

    draft_path.write_text(draft_text)

    print("\n" + "=" * 60)
    print(f"New draft saved for review: {draft_path}")
    print("=" * 60)
    print(draft_text)

    return draft_path

    # --- Optional: also email the draft to yourself so it lands in your
    # own inbox instead of/in addition to a local file. Still a draft,
    # still requires you to copy it into a message to the real recipient.
    #
    # import smtplib
    # from email.message import EmailMessage
    # msg = EmailMessage()
    # msg["Subject"] = f"[DRAFT] Ocean SF Outreach: {lead.get('name')}"
    # msg["From"] = os.environ["SENDER_EMAIL"]
    # msg["To"] = os.environ["RECIPIENT_EMAIL"]  # your own address
    # msg.set_content(draft_text)
    # with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
    #     s.login(os.environ["SENDER_EMAIL"], os.environ["SENDER_APP_PASSWORD"])
    #     s.send_message(msg)


def main():
    lead = run_agent()
    save_lead(lead)
    deliver_lead(lead)


if __name__ == "__main__":
    main()
