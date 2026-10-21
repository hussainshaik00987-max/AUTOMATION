"""Lead formatting and inline keyboard builder for Telegram Review Bot."""
from typing import Dict, Any, List, Optional
try:
    from telegram_bot.telegram_compat import InlineKeyboardButton, InlineKeyboardMarkup
except ImportError:
    from .telegram_compat import InlineKeyboardButton, InlineKeyboardMarkup


def determine_service_and_gap(lead: Dict[str, Any]) -> tuple[str, str, str]:
    """Determine the recommended service, gap summary, and pitch focus based on lead signals.

    Returns: (service_name, gap_summary, draft_hook)
    """
    website = (lead.get("website") or "").strip()
    pitch_angle = (lead.get("pitch_angle") or "").strip()
    site_status = lead.get("site_status")
    site_responsive = lead.get("site_responsive")
    automation_score = lead.get("automation_score") or 0
    instagram = (lead.get("instagram") or "").strip()

    # 1. No website or social profile acting as website
    if not website or "instagram.com" in website.lower() or "facebook.com" in website.lower():
        if instagram:
            return (
                "Custom Website Development & Search Presence",
                "Operating without an independent official website (relying on social profile).",
                "your official link points to social media rather than a dedicated portfolio website",
            )
        return (
            "Independent Business Website & Google Search Visibility",
            "No discoverable official website found.",
            "customers searching for your business cannot find an official website to browse packages and book directly",
        )

    # 2. Existing site is broken or not responsive
    if site_responsive in (0, "no", "False") or (site_status and site_status != 200):
        return (
            "Mobile-Responsive Website Modernization",
            "Existing website is not mobile-friendly or experiencing HTTP issues.",
            "your current website is difficult to browse on mobile phones where over 80% of local customers search",
        )

    # 3. High enquiry / WhatsApp automation fit
    if automation_score >= 60:
        return (
            "WhatsApp Business Automation & Inquiry Management",
            "High customer review volume with manual enquiry handling.",
            "responding to customer inquiries manually can lead to delayed replies and lost bookings",
        )

    # 4. General fallback
    return (
        "Digital Presence & Local Customer Growth",
        pitch_angle or "Opportunity to modernize digital presentation and streamline inquiries.",
        "there is a genuine opportunity to upgrade your digital showcase and capture more direct client inquiries",
    )


def format_lead_message(lead: Dict[str, Any], review_status: Optional[str] = None) -> str:
    """Format a lead into a clean, evidence-based Telegram notification message.

    Includes only available fields. Clearly marks draft outreach messages.
    """
    # Lead identification
    name = lead.get("name") or "Unknown Business"
    category = lead.get("primary_cat") or lead.get("vertical") or "Local Business"
    city = lead.get("search_city") or lead.get("city_state") or "Local Market"
    area = lead.get("area")

    loc_str = f"{area}, {city}" if area and area != city else city

    lines = [
        f"🏢 *{name}*",
        f"📂 *Category:* {category}",
        f"📍 *Location:* {loc_str}",
    ]

    # Contact details (only if present)
    contact_parts = []
    if lead.get("phone"):
        contact_parts.append(f"📞 {lead.get('phone')}")
    if lead.get("whatsapp"):
        contact_parts.append(f"💬 [WhatsApp]({lead.get('whatsapp')})")
    if lead.get("email"):
        contact_parts.append(f"✉️ {lead.get('email')}")
    if contact_parts:
        lines.append(f"📲 *Contact:* {' | '.join(contact_parts)}")

    # Links (only if present)
    link_parts = []
    if lead.get("website"):
        link_parts.append(f"🌐 [Website]({lead.get('website')})")
    if lead.get("instagram"):
        ig_followers = lead.get("ig_followers")
        follower_info = f" ({ig_followers:,} followers)" if ig_followers else ""
        link_parts.append(f"📸 [Instagram]({lead.get('instagram')}){follower_info}")
    if lead.get("facebook"):
        link_parts.append(f"📘 [Facebook]({lead.get('facebook')})")
    if link_parts:
        lines.append(f"🔗 *Links:* {' | '.join(link_parts)}")
    elif not lead.get("website"):
        lines.append("🌐 *Website:* _No official website discovered_")

    # Score and Evidence
    score = lead.get("score")
    score_line = f"📊 *Lead Score:* {score}" if score is not None else "📊 *Lead Score:* Unscored"
    if lead.get("segment"):
        score_line += f" (Tier: {lead.get('segment')})"
    lines.append(score_line)

    service, gap_summary, draft_hook = determine_service_and_gap(lead)

    evidence_items = []
    if gap_summary:
        evidence_items.append(f"• {gap_summary}")
    if lead.get("rating") is not None and lead.get("reviews") is not None:
        evidence_items.append(f"• Rating: {lead.get('rating')} ★ across {lead.get('reviews')} Google reviews")
    if lead.get("pitch_angle"):
        evidence_items.append(f"• Context: {lead.get('pitch_angle')}")

    if evidence_items:
        lines.append("🔍 *Evidence & Opportunity:*")
        lines.extend(evidence_items)

    # Recommended service
    lines.append(f"💡 *Recommended Service:* {service}")

    # Draft Outreach Message (Clearly labeled as draft)
    draft_msg = (
        f"\"Hi {name}, noticed your great work in {city}! We saw you have wonderful customer reviews, "
        f"but {draft_hook}. "
        f"We help local businesses set up direct booking channels so clients reach you effortlessly. "
        f"Would you be open to checking out a quick preview?\""
    )

    lines.append("\n📝 *DRAFT OUTREACH (NOT SENT — FOR REVIEW ONLY):*")
    lines.append(f"_{draft_msg}_")

    # Current status banner if already reviewed
    if review_status and review_status != "pending":
        status_badges = {
            "approved": "✅ APPROVED (Eligible for future outreach workflow)",
            "rejected": "❌ REJECTED (Excluded from outreach)",
            "review_later": "⏳ REVIEW LATER (Pending re-evaluation)",
        }
        badge = status_badges.get(review_status, review_status.upper())
        lines.append(f"\n📌 *Current Decision:* {badge}")

    return "\n".join(lines)


def build_lead_keyboard(lead_id: int) -> InlineKeyboardMarkup:
    """Build the inline action buttons for an unreviewed lead."""
    buttons = [
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"review:approve:{lead_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"review:reject:{lead_id}"),
            InlineKeyboardButton("⏳ Review Later", callback_data=f"review:review_later:{lead_id}"),
        ]
    ]
    return InlineKeyboardMarkup(buttons)


def build_decision_keyboard(status: str) -> InlineKeyboardMarkup:
    """Build a static display keyboard showing the recorded decision."""
    badges = {
        "approved": "✅ Approved",
        "rejected": "❌ Rejected",
        "review_later": "⏳ Marked: Review Later",
    }
    label = badges.get(status, f"Status: {status.title()}")
    buttons = [
        [InlineKeyboardButton(label, callback_data=f"noop:{status}")]
    ]
    return InlineKeyboardMarkup(buttons)
