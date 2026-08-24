import base64
import base64
import hmac
import html as html_lib
import json
import math
import os
import re
import shutil
import time
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo
import subprocess
import tempfile
import wave

import streamlit as st
import streamlit.components.v1 as components
from google import genai
from google.genai import types as genai_types
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
try:
    from supabase import Client as SupabaseClient
    from supabase import create_client
except ImportError:
    SupabaseClient = object
    create_client = None
DIRECT_VIDEO_EDITOR = components.declare_component(
    "one_team_direct_video_editor",
    path=str(Path(__file__).resolve().parent / "direct_video_editor_component"),
)

st.set_page_config(page_title="One Team · Movie Recap", page_icon="🎬", layout="wide", initial_sidebar_state="collapsed")

VOICE_OPTIONS = {
    "Kore": "Calm narrator",
    "Puck": "Bright and energetic",
    "Aoede": "Expressive storyteller",
    "Charon": "Deep cinematic narrator",
    "Fenrir": "Dramatic narrator",
    "Enceladus": "Warm and breathy",
    "Orus": "Clear and steady",
    "Schedar": "Confident presenter",
}
LANGUAGES = ["Burmese (မြန်မာ)", "English", "Thai", "Indonesian", "Vietnamese"]
PLATFORM_OPTIONS = {
    "YouTube": {"ratio": "16:9", "width": 1920, "height": 1080},
    "TikTok": {"ratio": "9:16", "width": 1080, "height": 1920},
    "Facebook": {"ratio": "1:1", "width": 1080, "height": 1080},
}
SUBTITLE_PIPELINE_VERSION = "unicode-burmese-v6-noto-safe"
SIMPLE_FREE_MAX_SECONDS = 180
PAID_PLAN_MAX_SECONDS = 180
CREDIT_VIDEO_MAX_SECONDS = 1800
CREDITS_FOR_BASE_VIDEO = 6
CREDIT_BASE_SECONDS = 180
CREDITS_PER_EXTRA_MINUTE = 2
PAID_PLAN_OFFERS = {
    "start": {"label": "One Start VIP", "price": 45000, "daily_limit": 1, "days": 30, "description": "3 min အထိ · တစ်နေ့ 1 Final Video · Owner API"},
    "creator": {"label": "One Creator VIP", "price": 85000, "daily_limit": 2, "days": 30, "description": "3 min အထိ · တစ်နေ့ 2 Final Videos · 1080p"},
    "studio": {"label": "One Studio VIP", "price": 195000, "daily_limit": 5, "days": 30, "description": "3 min အထိ · တစ်နေ့ 5 Final Videos · Priority"},
}
CREDIT_PACKS = {
    "starter": {"label": "30 Credits", "credits": 30, "price": 10000, "days": 30, "description": "3 min Video 5 ပုဒ်အထိ"},
    "basic": {"label": "60 Credits", "credits": 60, "price": 20000, "days": 30, "description": "3 min Video 10 ပုဒ်အထိ"},
    "creator": {"label": "120 Credits", "credits": 120, "price": 37000, "days": 60, "description": "3 min Video 20 ပုဒ်အထိ"},
    "studio": {"label": "240 Credits", "credits": 240, "price": 70000, "days": 90, "description": "3 min Video 40 ပုဒ်အထိ"},
}
SIMPLE_PAYMENT_DESTINATIONS = {
    "KBZPay": {"phone": "09670132806", "account_name": "Nay Lin Aung"},
    "WavePay": {"phone": "90670132806", "account_name": "Nay Lin Aung"},
}
PAYMENT_RECEIPT_BUCKET = "payment-receipts"
MAX_PAYMENT_RECEIPT_BYTES = 5 * 1024 * 1024


def secret_value(section: str, name: str) -> str:
    """Read a deployment secret without exposing it in the UI or repository."""
    try:
        nested = st.secrets.get(section, {})
        value = nested.get(name, "") if hasattr(nested, "get") else ""
    except Exception:
        value = ""
    return str(value or os.getenv(f"{section}_{name}".upper(), "")).strip()


def owner_api_key() -> str:
    try:
        configured = st.secrets.get("GOOGLE_AI_API_KEY", "")
    except Exception:
        configured = ""
    return str(configured or os.getenv("GOOGLE_AI_API_KEY", "")).strip()


def get_api_key() -> str:
    """Use a Simple member's own key; reserve the owner key only for approved Pro members."""
    plan = str((st.session_state.get("current_member") or {}).get("effective_plan", ""))
    if plan == "simple":
        return str(st.session_state.get("google_ai_key", "")).strip()
    if plan == "pro":
        return owner_api_key()
    return ""


def membership_secret(name: str) -> str:
    return secret_value("membership", name)


@st.cache_resource(show_spinner=False)
def get_supabase_client() -> SupabaseClient | None:
    url = membership_secret("supabase_url")
    service_key = membership_secret("supabase_service_key")
    if not url or not service_key or create_client is None:
        return None
    return create_client(url, service_key)


def get_google_identity() -> dict | None:
    """Return stable Google identity attributes supplied by Streamlit OIDC."""
    try:
        if not st.user.is_logged_in:
            return None
        identity = dict(st.user)
    except Exception:
        return None
    subject = str(identity.get("sub") or identity.get("email") or "").strip()
    email = str(identity.get("email") or "").strip().lower()
    if not subject or not email:
        return None
    return {
        "google_subject": subject,
        "email": email,
        "display_name": str(identity.get("name") or email).strip(),
        "avatar_url": str(identity.get("picture") or "").strip(),
    }


def google_login_configured() -> bool:
    try:
        auth = st.secrets.get("auth", {})
        return bool(auth.get("client_id") and auth.get("client_secret") and auth.get("redirect_uri") and auth.get("cookie_secret"))
    except Exception:
        return False


def parse_member_expiry(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def effective_member_plan(member: dict | None, now: datetime | None = None) -> str:
    if not isinstance(member, dict) or member.get("status") != "active":
        return "none"
    plan = str(member.get("plan") or "none")
    if plan not in {"simple", "pro"}:
        return "none"
    expiry = parse_member_expiry(member.get("plan_expires_at"))
    reference = now or datetime.now(timezone.utc)
    if expiry and reference >= expiry:
        return "none"
    return plan


def member_credit_balance(member: dict | None, now: datetime | None = None) -> int:
    """Return spendable credits and hide expired credits without mutating data during render."""
    if not isinstance(member, dict) or str(member.get("status")) != "active":
        return 0
    expiry = parse_member_expiry(member.get("credit_expires_at"))
    reference = now or datetime.now(timezone.utc)
    if expiry and reference >= expiry:
        return 0
    return max(0, int(member.get("credit_balance") or 0))


def credits_for_duration(duration_seconds: float) -> int:
    """3 minutes costs 6 credits; every additional started minute costs 2 credits."""
    seconds = max(1, int(math.ceil(float(duration_seconds or 0))))
    if seconds <= CREDIT_BASE_SECONDS:
        return CREDITS_FOR_BASE_VIDEO
    extra_minutes = int(math.ceil((seconds - CREDIT_BASE_SECONDS) / 60.0))
    return CREDITS_FOR_BASE_VIDEO + (extra_minutes * CREDITS_PER_EXTRA_MINUTE)


def active_subscription_tier(member: dict | None) -> str:
    tier = str((member or {}).get("subscription_tier") or "").strip().lower()
    if tier in PAID_PLAN_OFFERS:
        return tier
    return "start" if str((member or {}).get("effective_plan")) == "pro" else ""


def daily_plan_limit(member: dict | None) -> int:
    tier = active_subscription_tier(member)
    return int(PAID_PLAN_OFFERS.get(tier, {}).get("daily_limit", 0))


def offer_label(offer: dict) -> str:
    return f"{offer['label']} · {offer['price']:,} MMK"


def find_member(google_subject: str) -> dict | None:
    client = get_supabase_client()
    if client is None:
        return None
    try:
        rows = client.table("members").select("*").eq("google_subject", google_subject).limit(1).execute().data or []
        return dict(rows[0]) if rows else None
    except Exception:
        return None


def is_configured_admin_identity(identity: dict | None) -> bool:
    configured_email = membership_secret("admin_email").lower()
    identity_email = str((identity or {}).get("email", "")).strip().lower()
    return bool(configured_email and identity_email and hmac.compare_digest(identity_email, configured_email))


def bootstrap_designated_admin(identity: dict) -> dict | None:
    """Create or repair only the configured owner account; ordinary users still need Turnstile approval."""
    if not is_configured_admin_identity(identity):
        return None
    client = get_supabase_client()
    if client is None:
        return None
    now = datetime.now(timezone.utc).isoformat()
    subject = identity["google_subject"]
    try:
        existing = find_member(subject)
        if existing:
            client.table("members").update({
                "status": "active", "role": "admin", "approved_at": now,
                "admin_note": "Configured administrator bootstrap",
            }).eq("google_subject", subject).execute()
        else:
            client.table("members").insert({
                **identity,
                "status": "active",
                "plan": "none",
                "role": "admin",
                "approved_at": now,
                "admin_note": "Configured administrator bootstrap",
            }).execute()
        member = find_member(subject)
        if member:
            record_member_audit(subject, "admin_bootstrapped", identity["email"])
        return member
    except Exception:
        return None


def record_member_audit(google_subject: str, action: str, actor: str, note: str = "") -> None:
    client = get_supabase_client()
    if client is None:
        return
    try:
        client.table("member_audit").insert({"google_subject": google_subject, "action": action, "actor": actor, "note": note}).execute()
    except Exception:
        pass


def create_free_simple_member(identity: dict, existing: dict | None = None) -> dict | None:
    """Give ordinary Google users free Simple access; only paid Pro access needs owner approval."""
    existing = existing or find_member(identity["google_subject"])
    if existing:
        if existing.get("status") == "suspended" or existing.get("role") == "admin":
            return existing
        if effective_member_plan(existing) == "none":
            client = get_supabase_client()
            if client is None:
                return existing
            try:
                client.table("members").update({
                    "status": "active", "plan": "simple", "plan_expires_at": None,
                    "approved_at": datetime.now(timezone.utc).isoformat(), "admin_note": "Simple Free access",
                }).eq("google_subject", identity["google_subject"]).execute()
                record_member_audit(identity["google_subject"], "simple_free_activated", identity["email"])
                return find_member(identity["google_subject"])
            except Exception:
                return existing
        return existing
    existing = find_member(identity["google_subject"])
    client = get_supabase_client()
    if client is None:
        return None
    try:
        created = client.table("members").insert({
            **identity, "status": "active", "plan": "simple",
            "approved_at": datetime.now(timezone.utc).isoformat(), "admin_note": "Simple Free access",
        }).execute().data or []
        member = dict(created[0]) if created else find_member(identity["google_subject"])
        if member:
            record_member_audit(identity["google_subject"], "simple_free_activated", identity["email"])
        return member
    except Exception:
        return None


def is_designated_admin(member: dict | None) -> bool:
    configured_email = membership_secret("admin_email").lower()
    member_email = str((member or {}).get("email", "")).strip().lower()
    return bool(
        configured_email
        and member_email
        and isinstance(member, dict)
        and str(member.get("role", "member")) == "admin"
        and hmac.compare_digest(member_email, configured_email)
    )


def member_can_enter_editor(member: dict | None) -> bool:
    """Admins manage membership without an export plan; members require an active Simple or Pro plan."""
    current = member or {}
    return bool(current.get("is_admin") or str(current.get("effective_plan", "none")) in {"simple", "pro"})


def load_members(status: str | None = None) -> list[dict]:
    client = get_supabase_client()
    if client is None:
        return []
    try:
        query = client.table("members").select("*").order("registered_at", desc=False)
        if status:
            query = query.eq("status", status)
        return [dict(row) for row in (query.execute().data or [])]
    except Exception:
        return []


def approve_member(google_subject: str, plan: str, days: int, actor: str, note: str = "") -> bool:
    client = get_supabase_client()
    if client is None or plan not in {"simple", "pro"}:
        return False
    expiry = datetime.now(timezone.utc) + timedelta(days=max(1, int(days)))
    try:
        client.table("members").update({
            "status": "active", "plan": plan, "plan_expires_at": expiry.isoformat(),
            "approved_at": datetime.now(timezone.utc).isoformat(), "admin_note": note.strip(),
        }).eq("google_subject", google_subject).execute()
        record_member_audit(google_subject, f"approved_{plan}", actor, note)
        return True
    except Exception:
        return False


def suspend_member(google_subject: str, actor: str, note: str = "") -> bool:
    client = get_supabase_client()
    if client is None:
        return False
    try:
        client.table("members").update({"status": "suspended", "plan": "none", "admin_note": note.strip()}).eq("google_subject", google_subject).execute()
        record_member_audit(google_subject, "suspended", actor, note)
        return True
    except Exception:
        return False


def pending_payment_request(google_subject: str, plan: str) -> dict | None:
    client = get_supabase_client()
    if client is None:
        return None
    try:
        rows = client.table("payment_requests").select("*").eq("google_subject", google_subject).eq("plan", plan).eq("status", "submitted").order("submitted_at", desc=True).limit(1).execute().data or []
        return dict(rows[0]) if rows else None
    except Exception:
        return None


def upload_payment_receipt(google_subject: str, receipt) -> tuple[str, str]:
    """Store only a small image receipt in the private Supabase bucket; return its key or an error."""
    if receipt is None:
        return "", ""
    content = receipt.getvalue()
    mime_type = str(getattr(receipt, "type", "") or "").lower()
    allowed_types = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
    if not content or len(content) > MAX_PAYMENT_RECEIPT_BYTES:
        return "", "Receipt ပုံက 5 MB အောက်ဖြစ်ရမယ်။"
    if mime_type not in allowed_types:
        return "", "Receipt ကို JPG, PNG, သို့မဟုတ် WEBP ပုံသာတင်ပါ။"
    client = get_supabase_client()
    if client is None:
        return "", "Database Setting မရသေးပါ။"
    receipt_key = f"{google_subject}/{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:10]}.{allowed_types[mime_type]}"
    try:
        client.storage.from_(PAYMENT_RECEIPT_BUCKET).upload(
            path=receipt_key,
            file=content,
            file_options={"content-type": mime_type, "upsert": "false"},
        )
        return receipt_key, ""
    except Exception:
        return "", "Receipt ပုံကိုသိမ်းမရသေးပါ။ ခဏစောင့်ပြီးပြန်တင်ပါ။"


def create_vip_payment_request(member: dict, plan: str, payment_method: str, transaction_id: str, receipt=None) -> tuple[bool, str]:
    if plan != "pro":
        return False, "Payment Request က Pro VIP အတွက်သာပါ။"
    if payment_method not in SIMPLE_PAYMENT_DESTINATIONS:
        return False, "Payment Method ကိုရွေးပါ။"
    cleaned_transaction_id = re.sub(r"\s+", "", transaction_id or "")[:100]
    if len(cleaned_transaction_id) < 4:
        return False, "Transaction ID ကိုမှန်မှန်ထည့်ပါ။"
    existing = pending_payment_request(str(member.get("google_subject", "")), plan)
    if existing:
        return False, "ဒီ VIP Plan အတွက် ငွေလွှဲစစ်ဆေးရန်တောင်းဆိုထားပြီးပါပြီ။ Admin အတည်ပြုမှုကိုစောင့်ပါ။"
    receipt_key, receipt_error = upload_payment_receipt(str(member.get("google_subject", "")), receipt)
    if receipt_error:
        return False, receipt_error
    client = get_supabase_client()
    if client is None:
        return False, "Database Setting မရသေးပါ။"
    try:
        client.table("payment_requests").insert({
            "google_subject": member["google_subject"],
            "plan": plan,
            "payment_method": payment_method,
            "transaction_id": cleaned_transaction_id,
            "receipt_key": receipt_key,
        }).execute()
        plan_label = "Simple VIP" if plan == "simple" else "Pro VIP"
        record_member_audit(member["google_subject"], "payment_submitted", str(member.get("email", "")), f"{plan_label} via {payment_method}")
        return True, f"ငွေလွှဲအချက်အလက်ပို့ပြီးပါပြီ။ Admin စစ်ဆေးပြီး {plan_label} ဖွင့်ပေးပါမယ်။"
    except Exception:
        return False, "ငွေလွှဲအချက်အလက်မသိမ်းမရသေးပါ။ ခဏစောင့်ပြီးပြန်စမ်းပါ။"


def payment_offer(kind: str, offer_key: str) -> dict | None:
    catalog = PAID_PLAN_OFFERS if kind == "plan" else CREDIT_PACKS if kind == "credits" else {}
    offer = catalog.get(offer_key)
    return dict(offer) if isinstance(offer, dict) else None


def create_plan_or_credit_payment_request(member: dict, kind: str, offer_key: str, payment_method: str, transaction_id: str, receipt=None) -> tuple[bool, str]:
    """Create a manual payment request without granting plan days or credits yet."""
    offer = payment_offer(kind, offer_key)
    if offer is None:
        return False, "ရွေးထားတဲ့ Plan / Credit Pack မမှန်ပါ။"
    if payment_method not in SIMPLE_PAYMENT_DESTINATIONS:
        return False, "Payment Method ကိုရွေးပါ။"
    cleaned_transaction_id = re.sub(r"\s+", "", transaction_id or "")[:100]
    if len(cleaned_transaction_id) < 4:
        return False, "Transaction ID ကိုမှန်မှန်ထည့်ပါ။"
    plan_value = "pro" if kind == "plan" else "credits"
    existing = pending_payment_request(str(member.get("google_subject", "")), plan_value)
    if existing:
        return False, "ငွေလွှဲအချက်အလက် စစ်ဆေးရန်တောင်းဆိုထားပြီးပါပြီ။ Admin အတည်ပြုမှုကိုစောင့်ပါ။"
    receipt_key, receipt_error = upload_payment_receipt(str(member.get("google_subject", "")), receipt)
    if receipt_error:
        return False, receipt_error
    client = get_supabase_client()
    if client is None:
        return False, "Database Setting မရသေးပါ။"
    try:
        client.table("payment_requests").insert({
            "google_subject": member["google_subject"], "plan": plan_value,
            "request_kind": kind, "requested_tier": offer_key if kind == "plan" else "",
            "requested_credits": int(offer.get("credits", 0)), "amount_mmk": int(offer["price"]),
            "payment_method": payment_method, "transaction_id": cleaned_transaction_id, "receipt_key": receipt_key,
        }).execute()
        record_member_audit(member["google_subject"], "payment_submitted", str(member.get("email", "")), f"{kind}:{offer['label']} via {payment_method}")
        return True, f"{offer['label']} ငွေလွှဲအချက်အလက်ပို့ပြီးပါပြီ။ Admin စစ်ဆေးပြီးဖွင့်ပေးပါမယ်။"
    except Exception:
        return False, "ငွေလွှဲအချက်အလက်မသိမ်းမရသေးပါ။ SQL migration ကိုစစ်ပြီးပြန်စမ်းပါ။"


def load_submitted_payment_requests() -> list[dict]:
    client = get_supabase_client()
    if client is None:
        return []
    try:
        rows = client.table("payment_requests").select("*").eq("status", "submitted").order("submitted_at", desc=False).execute().data or []
        return [dict(row) for row in rows]
    except Exception:
        return []


def payment_receipt_url(receipt_key: str) -> str:
    if not receipt_key:
        return ""
    client = get_supabase_client()
    if client is None:
        return ""
    try:
        response = client.storage.from_(PAYMENT_RECEIPT_BUCKET).create_signed_url(receipt_key, 600)
        return str(response.get("signedURL") or response.get("signedUrl") or "")
    except Exception:
        return ""


def approve_vip_payment(request_id: int, payment: dict, days: int, actor: str, note: str = "") -> bool:
    plan = str(payment.get("plan", ""))
    if plan != "pro" or not approve_member(str(payment.get("google_subject", "")), plan, days, actor, note):
        return False
    client = get_supabase_client()
    if client is None:
        return False
    try:
        client.table("payment_requests").update({
            "status": "approved",
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "reviewed_by": actor,
            "admin_note": note.strip(),
        }).eq("id", request_id).execute()
        record_member_audit(str(payment.get("google_subject", "")), "payment_approved", actor, f"{plan.title()} VIP payment #{request_id}")
        return True
    except Exception:
        return False


def approve_plan_or_credit_payment(request_id: int, payment: dict, actor: str, note: str = "") -> bool:
    """Approve once: either activate an Owner-API tier or grant a paid credit balance."""
    kind = str(payment.get("request_kind") or ("credits" if payment.get("plan") == "credits" else "plan"))
    subject = str(payment.get("google_subject", ""))
    client = get_supabase_client()
    if client is None or not subject:
        return False
    if kind == "credits":
        credits = max(0, int(payment.get("requested_credits") or 0))
        pack = next((item for item in CREDIT_PACKS.values() if int(item["credits"]) == credits), None)
        if credits <= 0 or pack is None:
            return False
        expiry = datetime.now(timezone.utc) + timedelta(days=int(pack["days"]))
        try:
            response = client.rpc("grant_member_credits", {
                "p_google_subject": subject, "p_credits": credits,
                "p_credit_expires_at": expiry.isoformat(), "p_payment_id": int(request_id),
                "p_note": note.strip() or f"{credits} credits payment approved",
            }).execute()
            if not getattr(response, "data", False):
                return False
            client.table("payment_requests").update({
                "status": "approved", "reviewed_at": datetime.now(timezone.utc).isoformat(),
                "reviewed_by": actor, "admin_note": note.strip(),
            }).eq("id", request_id).execute()
            record_member_audit(subject, "payment_approved", actor, f"{credits} credits approved")
            return True
        except Exception:
            return False
    tier = str(payment.get("requested_tier") or "start")
    offer = PAID_PLAN_OFFERS.get(tier)
    if offer is None:
        return False
    expiry = datetime.now(timezone.utc) + timedelta(days=int(offer["days"]))
    try:
        client.table("members").update({
            "status": "active", "plan": "pro", "subscription_tier": tier,
            "plan_expires_at": expiry.isoformat(), "approved_at": datetime.now(timezone.utc).isoformat(),
            "admin_note": note.strip() or offer["label"],
        }).eq("google_subject", subject).execute()
        client.table("payment_requests").update({
            "status": "approved", "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "reviewed_by": actor, "admin_note": note.strip(),
        }).eq("id", request_id).execute()
        record_member_audit(subject, "payment_approved", actor, f"{offer['label']} approved")
        return True
    except Exception:
        return False


def reject_payment_request(request_id: int, payment: dict, actor: str, note: str = "") -> bool:
    """Remove a fake or incorrect request from the approval queue while retaining a minimal audit record."""
    client = get_supabase_client()
    if client is None:
        return False
    try:
        client.table("payment_requests").update({
            "status": "rejected",
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "reviewed_by": actor,
            "admin_note": note.strip() or "Rejected by admin",
        }).eq("id", request_id).execute()
        record_member_audit(str(payment.get("google_subject", "")), "payment_rejected", actor, f"VIP payment #{request_id}")
        return True
    except Exception:
        return False


def render_pro_payment_form(member: dict) -> None:
    st.caption("VIP Plan က Owner API ဖြင့် 3 min Video ကို daily quota အတိုင်းထုတ်လို့ရပါတယ်။")
    st.caption("Plans & Credits မှာစျေးနှုန်းရွေးပြီး ငွေလွှဲအတည်ပြုမှုတောင်းနိုင်ပါတယ်။")
    for method, details in SIMPLE_PAYMENT_DESTINATIONS.items():
        st.markdown(f"**{method}** · `{details['phone']}` · {details['account_name']}")
    submitted = pending_payment_request(str(member.get("google_subject", "")), "pro")
    if submitted:
        st.info(f"Pro VIP ငွေလွှဲတောင်းဆိုမှု ပို့ပြီးပါပြီ။ {submitted.get('payment_method', '')} · ID: {submitted.get('transaction_id', '')}")
        return
    with st.form("pro_payment_request"):
        method = st.radio("ငွေလွှဲမည့် App", list(SIMPLE_PAYMENT_DESTINATIONS), horizontal=True, key="pro_payment_method")
        transaction_id = st.text_input("Transaction ID", max_chars=100, key="pro_payment_transaction")
        receipt = st.file_uploader("Receipt ပုံ (မထည့်လည်းရ)", type=["jpg", "jpeg", "png", "webp"], key="pro_payment_receipt")
        submitted_form = st.form_submit_button("Pro VIP ငွေလွှဲအချက်အလက် ပို့မယ်", type="primary", use_container_width=True)
    if submitted_form:
        success, message = create_vip_payment_request(member, "pro", method, transaction_id, receipt)
        if success:
            st.success(message)
            st.rerun()
        st.error(message)


@st.dialog("One Team · Plans & Credits")
def render_plan_purchase_dialog(member: dict) -> None:
    st.caption("Final MP4 အောင်မြင်မှသာ Daily quota / Credits ဖြတ်မယ်။ Error ဖြစ်ရင် မဖြတ်ဘဲ Retry လုပ်လို့ရပါတယ်။")
    kind_label = st.radio("ဝယ်မည့်အမျိုးအစား", ["VIP Plan", "Credits"], horizontal=True, key="purchase_kind")
    kind = "plan" if kind_label == "VIP Plan" else "credits"
    catalog = PAID_PLAN_OFFERS if kind == "plan" else CREDIT_PACKS
    labels = {offer_label(offer): key for key, offer in catalog.items()}
    selected_label = st.selectbox("Plan / Credit Pack", list(labels), key=f"purchase_offer_{kind}")
    offer_key = labels[selected_label]
    offer = catalog[offer_key]
    st.info(offer["description"])
    if kind == "credits":
        st.caption("3 min = 6 Credits · 4 min = 8 Credits · 5 min = 10 Credits · 30 min အထိ Credit နဲ့ထုတ်လို့ရတယ်။")
    else:
        st.caption("VIP Plan က 3 min အထိသာပါ။ Video အရှည်လိုလျှင် Credits သုံးပါ။")
    for method, details in SIMPLE_PAYMENT_DESTINATIONS.items():
        st.markdown(f"**{method}** · `{details['phone']}` · {details['account_name']}")
    pending = pending_payment_request(str(member.get("google_subject", "")), "pro" if kind == "plan" else "credits")
    if pending:
        st.info("ငွေလွှဲအချက်အလက် ပို့ပြီးပါပြီ။ Admin စစ်ဆေးမှုကိုစောင့်ပါ။")
        return
    with st.form(f"purchase_{kind}_{offer_key}"):
        method = st.radio("ငွေလွှဲမည့် App", list(SIMPLE_PAYMENT_DESTINATIONS), horizontal=True, key=f"purchase_method_{kind}")
        transaction_id = st.text_input("Transaction ID", max_chars=100, key=f"purchase_transaction_{kind}")
        receipt = st.file_uploader("Receipt ပုံ (မထည့်လည်းရ)", type=["jpg", "jpeg", "png", "webp"], key=f"purchase_receipt_{kind}")
        submitted = st.form_submit_button(f"{selected_label} ငွေလွှဲအချက်အလက် ပို့မယ်", type="primary", use_container_width=True)
    if submitted:
        success, message = create_plan_or_credit_payment_request(member, kind, offer_key, method, transaction_id, receipt)
        if success:
            st.success(message)
            st.rerun()
        st.error(message)


def render_pro_upgrade_dialog(member: dict) -> None:
    """Compatibility wrapper retained for existing plan actions."""
    render_plan_purchase_dialog(member)


def myanmar_export_day(now: datetime | None = None) -> str:
    return (now or datetime.now(ZoneInfo("Asia/Yangon"))).astimezone(ZoneInfo("Asia/Yangon")).date().isoformat()


def successful_exports_today(google_subject: str, plan: str, export_day: str | None = None) -> int:
    client = get_supabase_client()
    if client is None:
        return 999
    try:
        rows = client.table("export_usage").select("id").eq("google_subject", google_subject).eq("plan", plan).eq("outcome", "success").eq("export_day", export_day or myanmar_export_day()).execute().data or []
        return len(rows)
    except Exception:
        return 999


def pro_export_used_today(google_subject: str, export_day: str | None = None) -> bool:
    return successful_exports_today(google_subject, "pro", export_day) >= 1


def export_access_error(member: dict, duration_seconds: float) -> str | None:
    plan = str(member.get("effective_plan") or effective_member_plan(member))
    seconds = max(0.0, float(duration_seconds or 0))
    credits_required = credits_for_duration(seconds)
    if seconds > CREDIT_VIDEO_MAX_SECONDS:
        return "Credit Video က 30 မိနစ်အထိသာ ထုတ်လို့ရပါတယ်။"
    if seconds > PAID_PLAN_MAX_SECONDS:
        if not owner_api_key():
            return "Credit Video အတွက် Owner API Key ကို Admin က Streamlit Secrets မှာထည့်ရပါမယ်။"
        balance = member_credit_balance(member)
        if balance < credits_required:
            return f"ဒီ {format_duration(round(seconds))} Video အတွက် {credits_required} Credits လိုပါတယ်။ လက်ကျန် {balance} Credits ပဲရှိပါတယ်။"
        return None
    if plan == "simple":
        if not st.session_state.get("google_ai_key"):
            return "Simple Free အတွက် ကိုယ်ပိုင် Gemini Free API Key ကိုအရင်ထည့်ပါ။"
        if seconds > SIMPLE_FREE_MAX_SECONDS:
            return "Simple Free က 3 မိနစ်အထိသာ ထုတ်လို့ရပါတယ်။ Video အရှည်လိုလျှင် Credits ဝယ်ပါ။"
        if successful_exports_today(str(member.get("google_subject", "")), "simple") >= 1:
            return "Simple Free ရဲ့ ဒီနေ့ Final Video ၁ ပုဒ် ထုတ်ပြီးပါပြီ။ မနက်ဖြန်ပြန်ထုတ်ပါ၊ သို့မဟုတ် Credits ဝယ်ပါ။"
        return None
    if plan == "pro":
        if not owner_api_key():
            return "Pro VIP Owner API Key ကို Admin က Streamlit Secrets မှာ မထည့်ရသေးပါ။"
        if seconds > PAID_PLAN_MAX_SECONDS:
            return "VIP Plan က 3 မိနစ်အထိသာပါ။ Video အရှည်လိုလျှင် Credits ဝယ်ပါ။"
        limit = daily_plan_limit(member)
        if successful_exports_today(str(member.get("google_subject", "")), "pro") >= limit:
            return f"{PAID_PLAN_OFFERS[active_subscription_tier(member)]['label']} ရဲ့ ဒီနေ့ {limit} ပုဒ် Limit ပြည့်သွားပါပြီ။"
        return None
    return "VIP Access မရှိသေးပါ။ Admin အတည်ပြုမှုကို စောင့်ပါ။"


def register_successful_pro_export(member: dict, duration_seconds: float) -> bool:
    client = get_supabase_client()
    if client is None:
        return False
    duration = max(0.0, float(duration_seconds or 0))
    subject = str(member.get("google_subject", ""))
    if duration > PAID_PLAN_MAX_SECONDS:
        needed = credits_for_duration(duration)
        try:
            response = client.rpc("consume_member_credits", {
                "p_google_subject": subject, "p_credits": needed,
                "p_note": f"Successful {format_duration(round(duration))} credit export",
            }).execute()
            if not getattr(response, "data", False):
                return False
            client.table("export_usage").insert({
                "google_subject": subject, "plan": "pro", "subscription_tier": "credits",
                "export_day": myanmar_export_day(), "source_duration_seconds": round(duration, 2), "outcome": "success",
            }).execute()
            return True
        except Exception:
            return False
    plan = str(member.get("effective_plan"))
    if plan not in {"simple", "pro"}:
        return True
    try:
        client.table("export_usage").insert({
            "google_subject": subject, "plan": plan, "subscription_tier": active_subscription_tier(member),
            "export_day": myanmar_export_day(), "source_duration_seconds": round(duration, 2), "outcome": "success",
        }).execute()
        return True
    except Exception:
        return False


def render_member_admin() -> None:
    member = st.session_state.get("current_member") or {}
    with st.expander("Admin Members"):
        if not is_designated_admin(member):
            st.caption("Owner Google Account ဖြင့်သာ Member Approval ကို ဖွင့်နိုင်ပါတယ်။")
            return
        password = st.text_input("Admin Password", type="password", key="membership_admin_password")
        if st.button("Member Admin ဖွင့်မယ်", use_container_width=True, key="membership_admin_open"):
            st.session_state.membership_admin_unlocked = bool(get_admin_password()) and hmac.compare_digest(password, get_admin_password())
        if not st.session_state.get("membership_admin_unlocked"):
            return
        member_lookup = {str(row.get("google_subject", "")): row for row in load_members()}
        payment_requests = load_submitted_payment_requests()
        st.caption(f"VIP Payment Request: {len(payment_requests)}")
        if payment_requests:
            labels = {
                f"{member_lookup.get(str(row.get('google_subject')), {}).get('email', 'Unknown')} · {str(row.get('plan', '')).title()} · {row.get('payment_method')} · {row.get('transaction_id')}": row
                for row in payment_requests
            }
            selected_payment_label = st.selectbox("ငွေလွှဲစစ်ဆေးရန်", list(labels), key="simple_payment_review")
            selected_payment = labels[selected_payment_label]
            st.caption(f"Submitted: {selected_payment.get('submitted_at', '')}")
            receipt_url = payment_receipt_url(str(selected_payment.get("receipt_key", "")))
            if receipt_url:
                st.link_button("Receipt ပုံကြည့်မယ်", receipt_url, use_container_width=True)
            payment_kind = str(selected_payment.get("request_kind") or ("credits" if selected_payment.get("plan") == "credits" else "plan"))
            payment_plan = str(selected_payment.get("requested_tier") or selected_payment.get("plan", "simple")).title()
            payment_amount = int(selected_payment.get("amount_mmk") or 0)
            requested_credits = int(selected_payment.get("requested_credits") or 0)
            st.caption(f"Request: {payment_kind} · {payment_plan} · {payment_amount:,} MMK · Credits: {requested_credits}")
            payment_note = st.text_input("Payment Admin Note", key="simple_payment_note")
            review_left, review_right = st.columns(2)
            with review_left:
                approve_payment = st.button(f"စစ်ပြီး {payment_plan} ဖွင့်မယ်", type="primary", use_container_width=True, key="vip_payment_approve")
            with review_right:
                reject_payment = st.button("အတု/မှား · ဖျက်မယ်", use_container_width=True, key="vip_payment_reject")
            if approve_payment:
                if approve_plan_or_credit_payment(int(selected_payment["id"]), selected_payment, str(member.get("email", "")), payment_note):
                    st.success(f"{payment_plan} ဖွင့်ပြီးပါပြီ။")
                    st.rerun()
                st.error("Payment Approval မအောင်မြင်သေးပါ။")
            if reject_payment:
                if reject_payment_request(int(selected_payment["id"]), selected_payment, str(member.get("email", "")), payment_note):
                    st.success("အတု/မှား Payment Request ကိုဖျက်ပြီးပါပြီ။")
                    st.rerun()
                st.error("Payment Request ကိုဖျက်မရသေးပါ။")
        pending = load_members("pending")
        st.caption(f"Pending Account: {len(pending)}")
        if pending:
            labels = {f"{row.get('display_name') or row.get('email')} · {row.get('email')}": row for row in pending}
            selected_label = st.selectbox("Pending User", list(labels), key="member_pending_user")
            selected = labels[selected_label]
            plan_label = st.selectbox("VIP Plan", ["Simple VIP · 10,000", "Pro VIP · 25,000"], key="member_plan_choice")
            days = st.number_input("VIP Days", min_value=1, max_value=366, value=30, key="member_plan_days")
            note = st.text_input("Admin Note", key="member_admin_note")
            if st.button("Approve VIP", type="primary", use_container_width=True, key="member_approve"):
                plan = "simple" if plan_label.startswith("Simple") else "pro"
                if approve_member(selected["google_subject"], plan, int(days), str(member.get("email")), note):
                    st.success("VIP ဖွင့်ပြီးပါပြီ။")
                    st.rerun()
                st.error("VIP ဖွင့်မရသေးပါ။ Database Setting ကိုစစ်ပါ။")
        active_members = [row for row in load_members() if row.get("status") == "active"]
        if active_members:
            labels = {f"{row.get('display_name') or row.get('email')} · {str(row.get('plan')).title()}": row for row in active_members}
            selected_label = st.selectbox("Active Member", list(labels), key="member_active_user")
            if st.button("Suspend Selected", use_container_width=True, key="member_suspend"):
                if suspend_member(labels[selected_label]["google_subject"], str(member.get("email"))):
                    st.success("Account ပိတ်ပြီးပါပြီ။")
                    st.rerun()
        st.caption("Simple Free: User Key · VIP: 3 min Daily quota · Credits: 30 min အထိ success-only deduction")


def render_account_gate() -> dict:
    """Stop the editor until Google identity is approved; the configured owner bootstraps safely."""
    if not google_login_configured():
        st.error("Google Login စနစ်ကို ပြင်ဆင်နေပါတယ်။ Admin က Streamlit Secrets မှာ Google OAuth setting ထည့်ပြီးမှ ဝင်လို့ရပါမယ်။")
        st.stop()
    identity = get_google_identity()
    if identity is None:
        st.markdown("## One Team Movie Recap")
        st.caption("Google Account နဲ့ဝင်ပြီး Simple Free ကို ကိုယ်ပိုင် Gemini Free API Key နဲ့သုံးပါ။ Pro VIP ကိုသာ Admin စစ်ဆေးဖွင့်ပေးပါမယ်။")
        st.button("Google နဲ့ ဝင်မယ်", type="primary", use_container_width=True, on_click=st.login)
        st.stop()
    if get_supabase_client() is None:
        st.error("Account Database ကို ပြင်ဆင်နေပါတယ်။ Admin က Supabase Secret ထည့်ပြီး SQL schema ကို run ပေးရပါမယ်။")
        st.button("Log out", on_click=st.logout)
        st.stop()
    member = find_member(identity["google_subject"])
    if member is None and is_configured_admin_identity(identity):
        member = bootstrap_designated_admin(identity)
    if member is None:
        member = create_free_simple_member(identity)
        if member:
            st.success("Simple Free ဖွင့်ပြီးပါပြီ။ အပေါ်က Simple Free Button ကိုနှိပ်ပြီး ကိုယ်ပိုင် Gemini API Key ထည့်ပါ။")
            st.rerun()
        st.error("Account သိမ်းမရသေးပါ။ ခဏစောင့်ပြီး ပြန်စမ်းပါ။")
        st.button("Log out", on_click=st.logout)
        st.stop()
    member = create_free_simple_member(identity, member) or member
    member["effective_plan"] = effective_member_plan(member)
    member["is_admin"] = is_designated_admin(member)
    st.session_state.current_member = member
    if not member_can_enter_editor(member):
        if member.get("status") == "suspended":
            st.error("Account ကို ခေတ္တပိတ်ထားပါတယ်။ Admin ကိုဆက်သွယ်ပါ။")
        elif member.get("status") == "pending":
            st.info("Account ကို Simple Free အဖြစ်ဖွင့်နေပါတယ်။ ခဏစောင့်ပြီးပြန်ဝင်ပါ။")
        else:
            st.warning("VIP သက်တမ်းကုန်သွားပါပြီ။ Admin ကိုဆက်သွယ်ပြီး Renew လုပ်ပါ။")
        st.button("Log out", on_click=st.logout)
        st.stop()
    return member


def render_public_policy_view(view: str) -> None:
    """Public pages stay outside the Google account gate for the OAuth consent screen."""
    st.markdown("# One Team")
    st.caption("Movie Recap Studio")
    if view == "privacy":
        st.markdown("## Privacy Policy")
        st.markdown(
            """One Team stores the minimum information needed to operate member access: your Google account identifier, email address, display name, plan status, plan expiry, and successful Pro export usage. These records are used to approve accounts, apply membership access, and enforce daily usage limits.

When you upload a video, it is processed only to create the requested recap output. One Team does not sell personal information. Access to member records is restricted to the app administrator and the secured service used to operate the app.

You may request account review, correction, or removal by contacting the One Team administrator. This policy may be updated when the service changes."""
        )
    else:
        st.markdown("## Terms of Service")
        st.markdown(
            """By using One Team, you confirm that you have the rights and permission to upload and process your videos. You are responsible for the content you upload, the scripts you generate, and how you use exported videos.

One Team may suspend accounts that misuse the service, attempt to bypass limits, or submit unlawful or infringing material. Membership access, export limits, and available features may change when the service is updated. The service is provided without a guarantee that every export or third-party AI request will succeed.

If you do not agree with these terms, do not use One Team."""
        )
    st.divider()
    st.markdown("[Back to One Team](./)")


DEFAULT_ADMIN_PASSWORD = "Khant@6789"


def get_admin_password() -> str:
    try:
        configured = str(st.secrets.get("ADMIN_PASSWORD", "")).strip()
    except Exception:
        configured = ""
    return configured or os.getenv("ADMIN_PASSWORD", "").strip() or DEFAULT_ADMIN_PASSWORD


METRICS_PATH = Path(__file__).resolve().parent / "generation_metrics.json"


def load_generation_log() -> list[str]:
    try:
        if not METRICS_PATH.exists():
            return []
        data = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
        return [str(value) for value in data.get("generation_log", []) if isinstance(value, str)]
    except (OSError, ValueError, TypeError):
        return []


def save_generation_log(log: list[str]) -> None:
    temporary_path = METRICS_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps({"generation_log": log}, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(METRICS_PATH)


def register_generation() -> None:
    log = load_generation_log()
    log.append(datetime.now(timezone.utc).isoformat())
    save_generation_log(log)


def generation_stats(log: list[str] | None = None, now: datetime | None = None) -> dict[str, int]:
    """Return valid recorded-generation counts without exposing any admin credentials."""
    reference_time = now or datetime.now(timezone.utc)
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)
    recent_count = 0
    valid_count = 0
    for stamp in log if log is not None else load_generation_log():
        try:
            parsed = datetime.fromisoformat(stamp)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        valid_count += 1
        if reference_time - parsed <= timedelta(hours=24):
            recent_count += 1
    return {"last_24_hours": recent_count, "total": valid_count}


def subtitle_style_signature(style: dict | None = None) -> tuple:
    """Return a stable signature for the subtitle style used by Final Video."""
    values = style or {
        "font": st.session_state.get("subtitle_font"),
        "size": st.session_state.get("subtitle_size", 34),
        "text_color": st.session_state.get("subtitle_text_color", "#FFFFFF"),
        "outline_color": st.session_state.get("subtitle_outline_color", "#000000"),
        "background_mode": st.session_state.get("subtitle_background_mode", "Transparent"),
        "background_color": st.session_state.get("subtitle_background_color", "#000000"),
        "background_opacity": st.session_state.get("subtitle_background_opacity", 55),
        "position": "Bottom",
        "x": st.session_state.get("subtitle_x", 50),
        "y": st.session_state.get("subtitle_y", 86),
    }
    return tuple(values.get(key) for key in ("font", "size", "text_color", "outline_color", "background_mode", "background_color", "background_opacity", "position", "x", "y"))


def render_menu() -> None:
    with st.popover("⚙️ Setting", use_container_width=True):
        st.markdown("### API Settings")
        st.caption("API Key ကို ဒီ Setting ထဲမှာသာထည့်ပါ။ Main page မှာ မပြပါ။")
        st.markdown("Google AI Studio Key ယူရန် [ဒီနေရာကိုဖွင့်ပါ](https://aistudio.google.com/app/apikey)")
        st.caption("Google AI Studio ရဲ့ AQ... Authentication Key နဲ့ AIza... legacy key နှစ်မျိုးလုံး ထည့်နိုင်ပါတယ်။ Key ကို Session အတွင်းသာ အသုံးပြုပြီး GitHub/URL ထဲ မသိမ်းပါ။")
        key = st.text_input("Google AI Studio API Key ထည့်ရန်", type="password", value=st.session_state.get("google_ai_key", ""), placeholder="AQ... or AIza...", key="menu_google_ai_key")
        if key.strip():
            st.session_state.google_ai_key = key.strip()
        if get_api_key():
            st.success("API Key အသင့်ဖြစ်ပါပြီ။")
        # Keep service configuration internal: users only manage their key here.
        st.session_state.setdefault("gemini_text_model", "gemini-3.7-flash")
        st.session_state.setdefault("gemini_tts_model", "gemini-3.1-flash-tts-preview")
        test_col, clear_col = st.columns(2)
        with test_col:
            if st.button("API စမ်းမယ်", use_container_width=True, key="menu_test_api"):
                try:
                    result = call_gemini(lambda client: client.interactions.create(model=st.session_state.gemini_text_model, input="Reply with the single word: READY"))
                    st.success(f"Connected: {getattr(result, 'output_text', 'READY')}")
                except Exception as exc:
                    st.error(api_error_message(exc))
        with clear_col:
            if st.button("Clear session key", use_container_width=True, key="menu_clear_key"):
                st.session_state.pop("google_ai_key", None)
                st.rerun()

        st.divider()
        st.markdown("### Admin Stats")
        st.caption("အသုံးပြုအကြိမ်ရေကြည့်ရန် Password ထည့်ပါ။ Password ကို App မှာမပြပါ။")
        admin_password = st.text_input("Admin Password", type="password", placeholder="Password ထည့်ပါ", key="menu_admin_password")
        expected_password = get_admin_password()
        if st.button("Admin Stats ဖွင့်မယ်", use_container_width=True, key="menu_admin_open"):
            st.session_state.admin_unlocked = bool(expected_password) and hmac.compare_digest(admin_password, expected_password)
            if not st.session_state.admin_unlocked:
                st.error("Password မမှန်ပါ။")
        if st.session_state.get("admin_unlocked"):
            stats = generation_stats()
            st.success(f"24 နာရီအတွင်း: {stats['last_24_hours']} ကြိမ် · စုစုပေါင်း: {stats['total']} ကြိမ်")
            if st.button("Admin ကိုပိတ်မယ်", use_container_width=True, key="menu_admin_close"):
                st.session_state.admin_unlocked = False
                st.session_state.pop("menu_admin_password", None)
                st.rerun()


def get_client() -> genai.Client:
    api_key = get_api_key().strip()
    if not api_key:
        raise ValueError("Google AI Studio API key မထည့်ရသေးပါ။ API Settings ထဲမှာ ထည့်ပါ။")
    # Cloud must surface a recoverable error instead of holding the export UI at
    # Script 8% indefinitely when an upstream AI request stops responding.
    return genai.Client(
        api_key=api_key,
        http_options=genai_types.HttpOptions(timeout=150000),
    )


def is_rate_limit_error(error: Exception) -> bool:
    lowered = str(error).lower()
    return any(token in lowered for token in ["429", "quota", "resource_exhausted", "rate limit", "too many requests"])


def retry_delay_seconds(error: Exception, attempt: int) -> float:
    """Use a server-provided retry hint when present, with a short capped fallback."""
    match = re.search(r"retry in\s+([\d.]+)s", str(error), flags=re.IGNORECASE)
    if match:
        try:
            return max(1.0, min(8.0, float(match.group(1)) + 0.35))
        except ValueError:
            pass
    return float(min(8, 2 ** (attempt + 1)))


def call_gemini(operation):
    last_error = None
    for attempt in range(3):
        try:
            return operation(get_client())
        except Exception as exc:
            last_error = exc
            retryable = is_rate_limit_error(exc) or "closed" in str(exc).lower()
            if not retryable or attempt == 2:
                raise
            time.sleep(retry_delay_seconds(exc, attempt))
    raise last_error


def api_error_message(error: Exception) -> str:
    message = str(error)
    lowered = message.lower()
    quota_error = is_rate_limit_error(error)
    key_error = any(token in lowered for token in ["401", "403", "api key", "unauthorized", "permission denied"])
    model_error = any(token in lowered for token in ["invalid_argument", "model not found", "unsupported model", "unknown model", "invalid model"])
    timeout_error = any(token in lowered for token in ["timeout", "timed out", "deadline exceeded", "readtimeout", "server disconnected"])
    if quota_error:
        return "AI request များလို့ ခဏစောင့်ပြီး အလိုအလျောက် 3 ကြိမ်ပြန်စမ်းပြီးပါပြီ။ 1–5 မိနစ်စောင့်ပြီး Video ထုတ်မယ်ကိုထပ်နှိပ်ပါ။"
    if key_error:
        st.session_state.pop("google_ai_key", None)
        return "API Key ကိုစစ်ပြီး ပြန်ထည့်ပါ။"
    if timeout_error:
        return "Script အချိန်မီမပြန်လို့ ရပ်လိုက်ပါပြီ။ Simple Free Button မှာ API Key ကိုစစ်ပြီး 1 မိနစ်နောက် Export Video ကိုတစ်ကြိမ်သာပြန်နှိပ်ပါ။"
    if model_error:
        return "AI စနစ် ခဏမရသေးပါ။ ခဏစောင့်ပြီးပြန်စမ်းပါ။"
    return "AI စနစ် ခဏမရသေးပါ။ ခဏစောင့်ပြီးပြန်စမ်းပါ။"


def save_upload(uploaded_file) -> Path:
    suffix = Path(uploaded_file.name).suffix or ".mp4"
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    handle.write(uploaded_file.getbuffer())
    handle.close()
    return Path(handle.name)


def activate_video_source(video_path: Path, display_name: str, source_token: str) -> None:
    """Activate an uploaded video and clear stale export state."""
    st.session_state.video_path = str(video_path)
    st.session_state.video_name = display_name
    st.session_state.video_source_token = source_token
    st.session_state.script = ""
    st.session_state.audio = None
    st.session_state.copyright_video_path = None
    st.session_state.blurred_video_path = None
    st.session_state.blur_masks = None
    st.session_state.blur_enabled = False
    st.session_state.subtitle_enabled = False
    st.session_state.pop("editor_frame_time", None)
    st.session_state.pop("direct_editor_refresh", None)
    st.session_state.output_video = None
    st.session_state.thumbnail_data = None
    st.session_state.thumbnail_title = None
    st.session_state.workflow_step = 1

def persist_logo_upload(uploaded_file) -> Path:
    """Create a compact circular PNG badge from a user logo for final video overlays."""
    logo_dir = Path(tempfile.gettempdir()) / "mgkhant-logos"
    logo_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(uploaded_file.name).name)
    logo_path = logo_dir / f"{os.getpid()}-{Path(safe_name).stem}-circle.png"
    try:
        with Image.open(BytesIO(uploaded_file.getbuffer())) as uploaded_image:
            source = uploaded_image.convert("RGBA")
            badge_size = 320
            inset = 12
            content = ImageOps.fit(source, (badge_size - inset * 2, badge_size - inset * 2), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
            badge = Image.new("RGBA", (badge_size, badge_size), (0, 0, 0, 0))
            mask = Image.new("L", (badge_size - inset * 2, badge_size - inset * 2), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, mask.width - 1, mask.height - 1), fill=255)
            badge.paste(content, (inset, inset), mask)
            ring = ImageDraw.Draw(badge)
            ring.ellipse((inset, inset, badge_size - inset - 1, badge_size - inset - 1), outline=(255, 255, 255, 235), width=5)
            badge.save(logo_path, format="PNG", optimize=True)
    except Exception as exc:
        raise ValueError(f"Logo ပုံကို အဝိုင်းပုံစံမပြောင်းနိုင်ပါ: {exc}") from exc
    return logo_path


def get_video_duration(video_path: Path) -> int | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        return max(1, round(float(result.stdout.strip())))
    except (subprocess.SubprocessError, ValueError, FileNotFoundError):
        return None


def parse_duration_input(value: str) -> int:
    cleaned = value.strip().lower().replace(".", ":")
    if ":" in cleaned:
        parts = cleaned.split(":")
        if len(parts) != 2 or not all(part.strip().isdigit() for part in parts):
            raise ValueError("အချိန်ကို 1:18 သို့မဟုတ် 0:45 ပုံစံနဲ့ ထည့်ပါ။")
        minutes, seconds = (int(part) for part in parts)
        if seconds >= 60:
            raise ValueError("စက္ကန့်ကို 00 မှ 59 အတွင်း ထည့်ပါ။")
        total = minutes * 60 + seconds
    elif cleaned.isdigit():
        total = int(cleaned)
    else:
        raise ValueError("အချိန်ကို 1:18 သို့မဟုတ် 1.18 ပုံစံနဲ့ ထည့်ပါ။")
    if total < 5:
        raise ValueError("Recap အရှည် အနည်းဆုံး 5 seconds ဖြစ်ရပါမယ်။")
    return total


def format_duration(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def seconds_to_srt_time(seconds: float) -> str:
    total_ms = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _unicode_clusters(text: str) -> list[str]:
    """Group a base character with following combining marks so Myanmar glyphs are never split."""
    clusters: list[str] = []
    current = ""
    for char in str(text or ""):
        category = unicodedata.category(char)
        if not current or (category.startswith("M") or char in "\u200c\u200d"):
            current += char
        else:
            clusters.append(current)
            current = char
    if current:
        clusters.append(current)
    return clusters


def wrap_subtitle_lines(text: str, max_chars: int = 34) -> list[str]:
    """Wrap Unicode/Myanmar text without splitting combining marks or producing long caption lines."""
    cleaned = re.sub(r"\s+", " ", str(text or "").replace("\r", "")).strip()
    if not cleaned:
        return []
    clusters = _unicode_clusters(cleaned)
    lines: list[str] = []
    current: list[str] = []
    last_break = -1
    for cluster in clusters:
        current.append(cluster)
        if cluster.isspace() or cluster in {"၊", "။", "၊", ",", ".", "!", "?", ":", ";"}:
            last_break = len(current)
        if len(current) > max(1, int(max_chars)):
            cut = last_break if last_break >= max(1, int(max_chars * 0.55)) else max(1, int(max_chars))
            line = "".join(current[:cut]).strip()
            if line:
                lines.append(line)
            current = current[cut:]
            while current and current[0].isspace():
                current.pop(0)
            last_break = -1
            for index, item in enumerate(current, start=1):
                if item.isspace() or item in {"၊", "။", "၊", ",", ".", "!", "?", ":", ";"}:
                    last_break = index
    tail = "".join(current).strip()
    if tail:
        lines.append(tail)
    return lines


def split_subtitle_segments(text: str, max_chars: int = 40) -> list[str]:
    """Split sentences on safe Unicode cluster boundaries before two-line SRT wrapping."""
    clean = re.sub(r"\s+", " ", str(text or "").replace("\r", "")).strip()
    if not clean:
        return []
    sentences = re.split(r"(?<=[။!?！？])\s*|\n+", clean)
    segments: list[str] = []
    segment_width = max(1, int(max_chars)) * 2
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        chunks = wrap_subtitle_lines(sentence, segment_width)
        segments.extend(chunks or [sentence])
    return segments or [clean]



SUBTITLE_MAX_LINE_CHARS = 16


def subtitle_timing_weight(caption: str) -> float:
    """Estimate speaking time from Unicode clusters and punctuation pauses."""
    weight = 0.0
    for cluster in _unicode_clusters(caption):
        if cluster.isspace():
            continue
        weight += 1.0
        if cluster in {"။", "၊", ".", ",", "!", "?", "!", "?"}:
            weight += 3.0
    return max(1.0, weight)


def subtitle_captions_from_script(script: str, lines_per_caption: int = 1) -> list[str]:
    segments = split_subtitle_segments(script)
    captions = []
    for segment in segments:
        wrapped = wrap_subtitle_lines(segment, SUBTITLE_MAX_LINE_CHARS)
        for index in range(0, len(wrapped), lines_per_caption):
            captions.append("\n".join(wrapped[index:index + lines_per_caption]))
    return captions


def script_to_srt(script: str, duration_seconds: float, lines_per_caption: int = 1) -> str:
    captions = subtitle_captions_from_script(script, lines_per_caption)
    if not captions:
        return ""
    duration = max(0.1, float(duration_seconds))
    weights = [subtitle_timing_weight(caption) for caption in captions]
    total_weight = max(1.0, sum(weights))
    entries = []
    elapsed = 0.0
    for index, caption in enumerate(captions):
        start = elapsed
        end = duration if index == len(captions) - 1 else elapsed + (duration * weights[index] / total_weight)
        entries.append(f"{index + 1}\n{seconds_to_srt_time(start)} --> {seconds_to_srt_time(end)}\n{caption}\n")
        elapsed = end
    return "\n".join(entries)


def narration_speech_windows(audio_bytes: bytes, sample_rate: int = 24000) -> list[tuple[float, float]]:
    """Find voiced regions in raw PCM narration; no external speech service is required."""
    if len(audio_bytes) < sample_rate // 5 * 2:
        return []
    samples = memoryview(audio_bytes[:len(audio_bytes) - (len(audio_bytes) % 2)]).cast("h")
    window_samples = max(120, int(sample_rate * 0.02))
    rms_values: list[float] = []
    for start in range(0, len(samples), window_samples):
        chunk = samples[start:start + window_samples]
        if not chunk:
            continue
        rms_values.append(math.sqrt(sum(value * value for value in chunk) / len(chunk)))
    if not rms_values:
        return []
    peak = max(rms_values)
    threshold = max(90.0, peak * 0.075)
    active = [value >= threshold for value in rms_values]
    # Short quiet gaps happen inside spoken phrases; only long pauses split cues.
    max_gap_windows = max(1, int(0.28 / 0.02))
    last_active = -1
    for index, is_active in enumerate(active):
        if is_active:
            if 0 < index - last_active - 1 <= max_gap_windows:
                for gap_index in range(last_active + 1, index):
                    active[gap_index] = True
            last_active = index
    windows: list[tuple[float, float]] = []
    start_index: int | None = None
    for index, is_active in enumerate(active + [False]):
        if is_active and start_index is None:
            start_index = index
        elif not is_active and start_index is not None:
            start_seconds = max(0.0, start_index * window_samples / sample_rate - 0.04)
            end_seconds = min(len(samples) / sample_rate, index * window_samples / sample_rate + 0.06)
            if end_seconds - start_seconds >= 0.12:
                windows.append((start_seconds, end_seconds))
            start_index = None
    return windows


def speech_fraction_to_time(windows: list[tuple[float, float]], fraction: float) -> float:
    total_speech = sum(max(0.0, end - start) for start, end in windows)
    if total_speech <= 0:
        return 0.0
    remaining = max(0.0, min(1.0, fraction)) * total_speech
    for start, end in windows:
        length = max(0.0, end - start)
        if remaining <= length:
            return start + remaining
        remaining -= length
    return windows[-1][1]


def script_to_audio_aligned_srt(script: str, audio_bytes: bytes, final_duration_seconds: float, lines_per_caption: int = 1) -> str:
    """Place captions along measured speech regions, then map them onto the chosen final timeline."""
    captions = subtitle_captions_from_script(script, lines_per_caption)
    raw_duration = len(audio_bytes) / (24000 * 2)
    final_duration = max(0.1, float(final_duration_seconds))
    windows = narration_speech_windows(audio_bytes)
    if not captions or raw_duration <= 0.1 or not windows:
        return script_to_srt(script, final_duration, lines_per_caption)
    weights = [subtitle_timing_weight(caption) for caption in captions]
    total_weight = max(1.0, sum(weights))
    scale = final_duration / raw_duration
    output: list[str] = []
    elapsed_weight = 0.0
    previous_end = 0.0
    for index, (caption, weight) in enumerate(zip(captions, weights), 1):
        start_fraction = elapsed_weight / total_weight
        elapsed_weight += weight
        end_fraction = elapsed_weight / total_weight
        start = speech_fraction_to_time(windows, start_fraction) * scale
        end = speech_fraction_to_time(windows, end_fraction) * scale
        start = max(previous_end, min(final_duration, start))
        end = max(start + 0.12, min(final_duration, end))
        if index == len(captions):
            end = max(start + 0.12, min(final_duration, end))
        output.append(f"{index}\n{seconds_to_srt_time(start)} --> {seconds_to_srt_time(end)}\n{caption}\n")
        previous_end = end
    return "\n".join(output)


def normalize_srt_text(srt_text: str) -> str:
    """Return strict UTF-8-friendly SRT blocks with monotonic timestamps and one visible line per caption."""
    cleaned = (srt_text or "").replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    blocks = []
    for raw_block in re.split(r"\n\s*\n", cleaned):
        lines = [line.strip() for line in raw_block.split("\n") if line.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        try:
            start_text, end_text = [part.strip() for part in lines[1].split("-->", 1)]
            start_match = re.match(r"(\d+):(\d{2}):(\d{2}),(\d{3})$", start_text)
            end_match = re.match(r"(\d+):(\d{2}):(\d{2}),(\d{3})$", end_text)
            if not start_match or not end_match:
                continue
            start_ms = (((int(start_match.group(1)) * 60) + int(start_match.group(2))) * 60 + int(start_match.group(3))) * 1000 + int(start_match.group(4))
            end_ms = (((int(end_match.group(1)) * 60) + int(end_match.group(2))) * 60 + int(end_match.group(3))) * 1000 + int(end_match.group(4))
            end_ms = max(start_ms + 250, end_ms)
            text_lines = wrap_subtitle_lines(" ".join(lines[2:]), SUBTITLE_MAX_LINE_CHARS)
            if not text_lines:
                continue
            cue_span = max(250, end_ms - start_ms)
            for cue_index, text_line in enumerate(text_lines):
                cue_start = start_ms + (cue_span * cue_index) // len(text_lines)
                cue_end = end_ms if cue_index == len(text_lines) - 1 else start_ms + (cue_span * (cue_index + 1)) // len(text_lines)
                blocks.append((cue_start, max(cue_start + 250, cue_end), text_line))
        except (TypeError, ValueError):
            continue
    normalized = []
    previous_end = 0
    for index, (start_ms, end_ms, text) in enumerate(blocks, 1):
        start_ms = max(previous_end, start_ms)
        end_ms = max(start_ms + 250, end_ms)
        normalized.append(f"{index}\n{seconds_to_srt_time(start_ms / 1000)} --> {seconds_to_srt_time(end_ms / 1000)}\n{text}\n")
        previous_end = end_ms
    return "\n".join(normalized)


def scale_srt_to_duration(srt_text: str, target_duration_seconds: float) -> str:
    """Rescale cue times to the final rendered voiceover duration without touching the audio."""
    normalized = normalize_srt_text(srt_text)
    target_ms = max(250, round(float(target_duration_seconds) * 1000))
    parsed: list[tuple[int, int, str]] = []
    for raw_block in re.split(r"\n\s*\n", normalized):
        lines = [line.strip() for line in raw_block.splitlines() if line.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        try:
            start_text, end_text = [part.strip() for part in lines[1].split("-->", 1)]
            def to_ms(value: str) -> int:
                match = re.match(r"(\d+):(\d{2}):(\d{2}),(\d{3})$", value)
                if not match:
                    raise ValueError("Invalid SRT timestamp")
                return (((int(match.group(1)) * 60 + int(match.group(2))) * 60 + int(match.group(3))) * 1000 + int(match.group(4)))
            parsed.append((to_ms(start_text), to_ms(end_text), " ".join(lines[2:])))
        except (TypeError, ValueError):
            continue
    if not parsed:
        return normalized
    source_ms = max(end for _start, end, _text in parsed)
    if source_ms <= 0:
        return normalized
    if abs(source_ms - target_ms) <= 10:
        return normalized
    factor = target_ms / source_ms
    output: list[str] = []
    previous_end = 0
    for index, (start, end, text) in enumerate(parsed, 1):
        scaled_start = max(previous_end, round(start * factor))
        scaled_end = max(scaled_start + 100, round(end * factor))
        if index == len(parsed):
            scaled_end = target_ms
        else:
            scaled_end = min(target_ms, scaled_end)
        output.append(f"{index}\n{seconds_to_srt_time(scaled_start / 1000)} --> {seconds_to_srt_time(scaled_end / 1000)}\n{text}\n")
        previous_end = scaled_end
    return "\n".join(output)


def srt_to_plain_text(srt_text: str) -> str:
    """Extract caption text from a time-coded SRT for the visual preview."""
    text_lines = []
    for line in (srt_text or "").replace("\r", "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.isdigit() or "-->" in stripped:
            continue
        text_lines.append(stripped)
    return " ".join(text_lines)


def add_srt_position_tags(srt_text: str, x_percent: int, y_percent: int, width: int, height: int) -> str:
    """Deprecated compatibility helper: preserve clean SRT and never inject visible ASS markup."""
    return normalize_srt_text(srt_text)


def extract_translation_audio(video_path: Path) -> Path:
    """Create a small speech-focused file so faithful translation uploads quickly."""
    audio_path = Path(tempfile.mktemp(suffix="-translation.mp3"))
    command = [
        "ffmpeg", "-y", "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "libmp3lame", "-b:a", "48k", str(audio_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    if result.returncode != 0 or not audio_path.exists() or audio_path.stat().st_size == 0:
        audio_path.unlink(missing_ok=True)
        raise RuntimeError(result.stderr[-800:] or "Video ထဲက အသံကို မထုတ်နိုင်ပါ။")
    return audio_path


def estimate_script_seconds(text: str, language: str) -> float:
    cleaned = " ".join(text.split())
    if not cleaned:
        return 0.0
    if language.startswith("Burmese"):
        # Burmese narration has no whitespace between every word, so estimate by
        # Myanmar characters rather than splitting on spaces.
        units = sum(1 for char in cleaned if not char.isspace())
        return units / 7.5
    return len(cleaned.split()) / 2.35


def complete_short_recap_script(script: str, target_language: str, target_seconds: int, tone: str) -> str:
    current_seconds = estimate_script_seconds(script, target_language)
    if current_seconds >= target_seconds * 0.88:
        return script

    missing_seconds = max(1, int(round(target_seconds - current_seconds)))
    completion_prompt = f"""
You are revising a movie recap narration that is too short.
Target language: {target_language}.
Target runtime: {target_seconds} seconds. Estimated current runtime: {current_seconds:.0f} seconds.
Add approximately {missing_seconds} seconds of narration while keeping the same story and tone ({tone}).
Expand with only visible or inferable scene details: character actions, facial and emotional reactions,
movement, setting changes, cause-and-effect, important objects, tension, and brief paraphrased dialogue context.
Do not invent scenes, do not quote source dialogue, do not add headings or timestamps, and return only the complete revised narration.

CURRENT NARRATION:
{script}
"""
    try:
        interaction = call_gemini(lambda client: client.interactions.create(
            model=st.session_state.get("gemini_text_model", "gemini-3.7-flash"),
            input=completion_prompt,
            generation_config={"temperature": 0.55, "thinking_level": "low"},
        ))
        expanded = (getattr(interaction, "output_text", None) or "").strip()
        if expanded and estimate_script_seconds(expanded, target_language) > current_seconds:
            return expanded
    except Exception:
        pass
    return script


def generate_recap_script(video_path: Path, language: str, duration_seconds: int, tone: str, mode: str) -> str:
    media_path = video_path
    media_type = "video"
    media_mime = "video/mp4"
    temporary_audio = None
    # Some AQ-compatible gateways incorrectly encode request metadata as ASCII.
    # Keep the target-language instruction ASCII-safe while retaining Burmese output intent.
    target_language = "Burmese (Myanmar)" if language.startswith("Burmese") else language
    if mode == "Faithful full translation":
        try:
            temporary_audio = extract_translation_audio(video_path)
            media_path = temporary_audio
            media_type = "audio"
            media_mime = "audio/mpeg"
        except Exception:
            # Keep the full-video fallback for files with no readable audio stream.
            temporary_audio = None

    try:
        uploaded = call_gemini(lambda client: client.files.upload(file=str(media_path)))
    finally:
        if temporary_audio:
            temporary_audio.unlink(missing_ok=True)

    if mode == "Faithful full translation":
        prompt = f"""
Watch the uploaded video and translate ALL spoken dialogue and narration into {target_language}.
This is a faithful translation mode: do not summarize, shorten, skip, reorder, or invent anything.
Preserve every meaningful sentence and event in the original order. Translate naturally and clearly for a native {target_language} speaker. If the target is Burmese (Myanmar), write the result using Myanmar Unicode script.
Keep speaker changes and paragraph breaks when they are apparent. Do not add commentary, headings, timestamps, subtitles, or explanations.
If a word is unclear, mark it as [unclear] rather than inventing content.
Return only the complete natural translation.
"""
    else:
        prompt = f"""
You are a professional movie recap editor. Watch the uploaded video and write a complete original narration in {target_language}.
The user selected an exact target runtime of {duration_seconds} seconds ({duration_seconds // 60} minutes {duration_seconds % 60} seconds). The narration must be long enough to fill that full runtime at a natural Burmese narration pace; do not produce a short summary.

Build the narration scene by scene from the video. Cover the visible actions, character movements, facial or emotional reactions, changes in location, cause-and-effect, important objects, tension, and the way each scene leads to the next. Include brief paraphrased context for important dialogue and how other characters respond, but never quote source dialogue word-for-word. Use connective narration between scenes so the final script feels continuous and complete.

Important originality and safety rules:
- Do not copy dialogue, subtitles, or any source narration word-for-word.
- Do not quote long passages.
- Paraphrase the events in your own words and focus on commentary, sequence, cause-and-effect, and character decisions.
- Do not invent scenes that are not visible or inferable from the video.
- Do not end early just because the main plot is known; continue through the selected runtime with concrete scene details and reactions.
- Return only the narration script, without headings, markdown, timestamps, or subtitles.
"""
    interaction = call_gemini(lambda client: client.interactions.create(
        model=st.session_state.get("gemini_text_model", "gemini-3.7-flash"),
        input=[
            {"type": "text", "text": prompt},
            {"type": media_type, "uri": uploaded.uri, "mime_type": getattr(uploaded, "mime_type", media_mime) or media_mime},
        ],
        generation_config={"temperature": 0.65, "thinking_level": "low"},
    ))
    text = getattr(interaction, "output_text", None)
    if not text:
        raise RuntimeError("Gemini က Script မပြန်ပေးပါ။")
    result = text.strip()
    if mode != "Faithful full translation":
        result = complete_short_recap_script(result, target_language, duration_seconds, tone)
    return result


def generate_voiceover(text: str, voice: str, style: str) -> bytes:
    prompt = f"Read this narration in a {style} style. Speak clearly and naturally, with short pauses at punctuation.\n\n{text}"
    interaction = call_gemini(lambda client: client.interactions.create(
        model=st.session_state.get("gemini_tts_model", "gemini-3.1-flash-tts-preview"),
        input=prompt,
        response_format={"type": "audio"},
        generation_config={"speech_config": [{"voice": voice}]},
    ))
    audio = getattr(interaction, "output_audio", None)
    encoded = getattr(audio, "data", None) if audio else None
    if not encoded:
        raise RuntimeError("Gemini က Audio မပြန်ပေးပါ။")
    return base64.b64decode(encoded)


def split_narration_for_tts(script: str, maximum_characters: int = 2600) -> list[str]:
    """Keep each Gemini TTS request safely below its audio-output limit for long recaps."""
    cleaned = re.sub(r"\s+", " ", str(script or "")).strip()
    if not cleaned:
        return []
    sentences = [piece.strip() for piece in re.split(r"(?<=[။!?])\s+|\n+", cleaned) if piece.strip()]
    chunks: list[str] = []
    current = ""
    for sentence in sentences or [cleaned]:
        if len(sentence) > maximum_characters:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(sentence[index:index + maximum_characters] for index in range(0, len(sentence), maximum_characters))
            continue
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > maximum_characters:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def generate_segmented_voiceover(script: str, voice: str, style: str, progress_callback=None) -> bytes:
    """Generate and concatenate compatible 24 kHz PCM narration segments for up to 30-minute recaps."""
    chunks = split_narration_for_tts(script)
    if not chunks:
        raise RuntimeError("Voice ထုတ်ရန် Script မရှိသေးပါ။")
    audio_segments: list[bytes] = []
    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        if progress_callback:
            percent = 35 + int((index - 1) * 20 / max(1, total))
            label = "Voiceover ပြင်နေသည်" if total == 1 else f"Voiceover အပိုင်း {index}/{total} ပြင်နေသည်"
            progress_callback(percent, label)
        audio_segments.append(generate_voiceover(chunk, voice, style))
    return b"".join(audio_segments)


THUMBNAIL_RATIO_OPTIONS = ["3:4", "9:16", "16:9"]
THUMBNAIL_PART_OPTIONS = ["မရွေးပါ", "အပိုင်း 1", "အပိုင်း 2", "အပိုင်း 3", "အပိုင်း 4"]


def thumbnail_story_excerpt(script: str, maximum_characters: int = 5200) -> str:
    """Keep a clean, bounded story source for an automatic thumbnail prompt."""
    compact = re.sub(r"\s+", " ", str(script or "")).strip()
    return compact[:maximum_characters]


def generate_thumbnail_title(script: str) -> str:
    """Create a short Burmese hook from the completed narration without user input."""
    story = thumbnail_story_excerpt(script, 4200)
    if not story:
        return "ဖုံးကွယ်ထားတဲ့ အမှန်တရား"
    prompt = f"""
Read this Burmese movie-recap narration and write ONE short, clickable Burmese Unicode
YouTube thumbnail headline. Use the most important visible conflict, secret, danger,
or surprising turn. Keep it under 15 Burmese words. Do not add quotation marks,
markdown, explanations, episode numbers, or claims not supported by the narration.

Narration:
{story}
"""
    interaction = call_gemini(lambda client: client.interactions.create(
        model=st.session_state.get("gemini_text_model", "gemini-3.7-flash"),
        input=prompt,
        generation_config={"temperature": 0.65, "thinking_level": "low"},
    ))
    title = re.sub(r"\s+", " ", str(getattr(interaction, "output_text", "") or "")).strip().strip("\"'“”")
    return title[:100] or "ဖုံးကွယ်ထားတဲ့ အမှန်တရား"


def build_thumbnail_prompt(story_script: str, title: str, aspect_ratio: str, part_label: str | None = None) -> str:
    """Describe a viral but story-grounded recap thumbnail for Gemini image generation."""
    story = thumbnail_story_excerpt(story_script)
    title_line = f"{part_label} · {title}" if part_label else title
    return f"""
Create one professional, hyper-realistic cinematic YouTube movie-recap thumbnail.
It must be a Korean-drama/movie-poster-inspired, high-engagement visual derived ONLY
from the story source below. Select the central protagonist, the most important scene,
and one clear clue, object, or danger from that story. Use a large emotional character
portrait in the foreground with sharp eyes, natural skin texture, dramatic rim lighting,
and a believable expression. Use a realistic cinematic background with depth, bokeh,
volumetric light, tense storytelling, and supporting scene elements.

Add one or two bold red arrows and a red circle to highlight the single important clue.
Use tasteful glow, vivid cyan/blue/orange contrast, readable mobile composition, and
professional news-recap thumbnail energy. Never use a real celebrity, logo, watermark,
or unrelated object. Do not fabricate story events that are not supported below.

Render this exact Burmese Unicode headline prominently near the lower third:
"{title_line}"
Use bold Burmese display typography with yellow, white, and cyan emphasis, thick black
outline and shadow. Do not render any other words, English letters, subtitles, episode
number, or small unreadable text. Keep the main portrait and clue visible.

Story source:
{story}

Aspect ratio: {aspect_ratio}. Output one finished vertical or landscape thumbnail image.
"""


def generate_ai_thumbnail(script: str, aspect_ratio: str, part_label: str | None = None) -> tuple[bytes, str]:
    """Generate one finished thumbnail image and its automatic Burmese title."""
    if aspect_ratio not in THUMBNAIL_RATIO_OPTIONS:
        raise ValueError("Thumbnail Ratio ကို 3:4၊ 9:16 သို့မဟုတ် 16:9 ထဲက ရွေးပါ။")
    title = generate_thumbnail_title(script)
    prompt = build_thumbnail_prompt(script, title, aspect_ratio, part_label)
    interaction = call_gemini(lambda client: client.interactions.create(
        model="gemini-3.1-flash-image",
        input=prompt,
        response_format={"type": "image", "mime_type": "image/jpeg", "aspect_ratio": aspect_ratio, "image_size": "1K"},
    ))
    image = getattr(interaction, "output_image", None)
    encoded = getattr(image, "data", None) if image else None
    if not encoded:
        raise RuntimeError("AI က Thumbnail ပုံမပြန်ပေးပါ။ ခဏစောင့်ပြီး ထပ်စမ်းပါ။")
    return base64.b64decode(encoded), title


def pcm_to_wav(pcm: bytes) -> bytes:
    output = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    output.close()
    with wave.open(output.name, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24000)
        wav_file.writeframes(pcm)
    data = Path(output.name).read_bytes()
    Path(output.name).unlink(missing_ok=True)
    return data


def extract_preview_frame(video_path: Path, at_seconds: float = 0) -> Image.Image:
    """Decode a preview frame with a seek fallback for mobile-uploaded videos."""
    commands = [
        ["ffmpeg", "-y", "-ss", str(max(0, at_seconds)), "-i", str(video_path), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
        ["ffmpeg", "-y", "-i", str(video_path), "-ss", str(max(0, at_seconds)), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
        ["ffmpeg", "-y", "-i", str(video_path), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
    ]
    last_error = ""
    for command in commands:
        try:
            result = subprocess.run(command, capture_output=True, text=False, timeout=90, check=True)
            if result.stdout:
                return Image.open(BytesIO(result.stdout)).convert("RGB")
            last_error = result.stderr.decode("utf-8", errors="ignore")[-500:]
        except (subprocess.SubprocessError, OSError) as exc:
            last_error = str(exc)
    raise RuntimeError(f"Video frame မရပါ။ Video format ကို စစ်ပါ။ {last_error}")


@st.cache_data(show_spinner=False)
def cached_subtitle_preview_frame(video_path_text: str, modified_ns: int) -> Image.Image:
    """Cache the first frame so X/Y style changes stay responsive on a phone."""
    del modified_ns
    return extract_preview_frame(Path(video_path_text), 0)


FONT_FILES = {
    # The second value must match the font's internal family metadata; it is
    # what libass receives in force_style=FontName=....
    "Noto Sans Myanmar": (None, "Noto Sans Myanmar"),
    "ပြည်ထောင်စု 2.5.3 Bold": ("ပြည်ထောင်စု-2.5.3_Bold.ttf", "Pyidaungsu"),
    "ပြည်ထောင်စု Bold": ("ပြည်ထောင်စု_Bold.ttf", "Pyidaungsu Green"),
    "ပြည်ထောင်စု Regular": ("ပြည်ထောင်စု_Regular.ttf", "Pyidaungsu Green"),
    # Keep the legacy label usable even though the bundle contains the 2.5.3
    # build rather than a separate 2.5 file.
    "ပြည်ထောင်စု 2.5 Bold": ("ပြည်ထောင်စု-2.5.3_Bold.ttf", "Pyidaungsu"),
    "ဧက၀၁ Bold": ("ဧက၀၁-Bold.ttf", "A ka 01"),
    "ဧက၀၇ Bold": ("ဧက၀၇-Bold.ttf", "A ka 07"),
} 

# Only present modern Unicode-capable families in the active subtitle menu.
# The legacy A ka faces stay bundled for old projects but can render Burmese
# shaping inconsistently in browser and libass environments.
SUBTITLE_FONT_OPTIONS = (
    "Noto Sans Myanmar",
    "ပြည်ထောင်စု 2.5.3 Bold",
    "ပြည်ထောင်စု Bold",
    "ပြည်ထောင်စု Regular",
)


def resolve_myanmar_font(font_name: str | None = None) -> Path | None:
    """Resolve a Unicode Myanmar font in both local and Streamlit Cloud layouts."""
    module_dir = Path(__file__).resolve().parent
    font_dirs = [module_dir / "fonts", Path.cwd() / "fonts", Path("/app/fonts")]
    # Streamlit Cloud may mount the repository under /mount/src/<repo>/.
    mount_root = Path("/mount/src")
    if mount_root.exists():
        font_dirs.extend(path for path in mount_root.glob("*/fonts") if path.is_dir())

    requested_filename = FONT_FILES.get(font_name, (None, None))[0]
    if requested_filename:
        for directory in font_dirs:
            candidate = directory / requested_filename
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate

    # Noto is a selectable family, not an alias for Pyidaungsu. Resolve it
    # before the bundled-font fallback so libass receives a matching family.
    if font_name == "Noto Sans Myanmar":
        noto_candidates = [
            Path("/usr/share/fonts/truetype/noto/NotoSansMyanmar-Regular.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSansMyanmar-Medium.ttf"),
            Path("/usr/share/fonts/opentype/noto/NotoSansMyanmar-Regular.ttf"),
        ]
        for root in (Path("/usr/share/fonts"), Path("/usr/local/share/fonts")):
            if root.is_dir():
                noto_candidates.extend(sorted(root.rglob("NotoSansMyanmar*.ttf")))
        for candidate in noto_candidates:
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
        try:
            matched = subprocess.check_output(
                ["fc-match", "-f", "%{file}", "Noto Sans Myanmar"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=3,
            ).strip()
            matched_path = Path(matched)
            if matched_path.is_file() and matched_path.stat().st_size > 0:
                return matched_path
        except (OSError, subprocess.SubprocessError):
            pass

    bundled_names = ("ပြည်ထောင်စု-2.5.3_Bold.ttf", "ပြည်ထောင်စု-2.5.ttf", "ပြည်ထောင်စု_Bold.ttf", "ပြည်ထောင်စု_Regular.ttf")
    for directory in font_dirs:
        for filename in bundled_names:
            candidate = directory / filename
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate

    system_candidates = [
        Path("/usr/share/fonts/truetype/noto/NotoSansMyanmar-Regular.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSansMyanmar-Medium.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansMyanmar-Regular.ttf"),
        Path("/usr/share/fonts/truetype/padauk/Padauk-Regular.ttf"),
    ]
    for root in (Path("/usr/share/fonts"), Path("/usr/local/share/fonts")):
        if root.is_dir():
            system_candidates.extend(sorted(root.rglob("NotoSansMyanmar*.ttf")))
            system_candidates.extend(sorted(root.rglob("Padauk*.ttf")))
    for candidate in system_candidates:
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    # Debian font packages can register a valid file under a different filename.
    try:
        matched = subprocess.check_output(
            ["fc-match", "-f", "%{file}", "Noto Sans Myanmar"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).strip()
        matched_path = Path(matched)
        if matched_path.is_file() and matched_path.stat().st_size > 0:
            return matched_path
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def font_file_family(font_path: Path | None) -> str | None:
    """Read the exact family metadata that libass must match in the staged font file."""
    if not font_path or not font_path.is_file():
        return None
    try:
        family = subprocess.check_output(
            ["fc-scan", "--format", "%{family}", str(font_path)],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).strip()
        return family.split(",", 1)[0].strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def resolved_font_family(font_name: str | None, font_path: Path | None) -> str:
    """Return the precise libass family from the selected staged font file."""
    return font_file_family(font_path) or subtitle_font_family(font_name)


def subtitle_font_family(font_name: str | None) -> str:
    return FONT_FILES.get(font_name, (None, font_name or "Pyidaungsu"))[1]


def render_live_subtitle_preview(frame: Image.Image, text: str, font_name: str, font_size: int, text_color: str, outline_color: str, background_mode: str, background_color: str, background_opacity: int, position: str, x_percent: int = 50, y_percent: int | None = None) -> Image.Image:
    """Render a low-cost visual sample for subtitle styling before final FFmpeg export."""
    image = frame.convert("RGB").copy()
    image.thumbnail((900, 900))
    draw = ImageDraw.Draw(image, "RGBA")
    font_path = resolve_myanmar_font(font_name)
    try:
        font = ImageFont.truetype(str(font_path), max(1, int(font_size))) if font_path else ImageFont.truetype("DejaVuSans.ttf", max(1, int(font_size)))
    except OSError:
        font = ImageFont.load_default()
    clean_text = " ".join((text or "မြန်မာစာတန်းထိုး စမ်းသပ်ခြင်း").split())
    clean_text = clean_text[:180]
    max_width = max(120, image.width - 48)
    estimated_chars = max(12, min(34, int(max_width / max(10, font_size * 0.58))))
    lines = wrap_subtitle_lines(clean_text, estimated_chars)[:1]
    line_gap = max(4, int(font_size * 0.18))
    line_boxes = [draw.textbbox((0, 0), line, font=font, stroke_width=2) for line in lines]
    block_height = sum(box[3] - box[1] for box in line_boxes) + line_gap * max(0, len(lines) - 1)
    if y_percent is None:
        if position == "Top":
            y_percent = 12
        elif position == "Center":
            y_percent = 50
        else:
            y_percent = 86
    start_y = max(8, min(image.height - block_height - 8, round(image.height * max(0, min(100, int(y_percent))) / 100 - block_height / 2)))
    if background_mode == "Solid background":
        pad_x, pad_y = 16, 10
        max_line_width = max((box[2] - box[0] for box in line_boxes), default=0)
        center_x = round(image.width * max(0, min(100, int(x_percent))) / 100)
        bg_box = (max(8, center_x - max_line_width // 2 - pad_x), max(4, start_y - pad_y), min(image.width - 8, center_x + max_line_width // 2 + pad_x), min(image.height - 4, start_y + block_height + pad_y))
        draw.rounded_rectangle(bg_box, radius=12, fill=background_color + f"{max(0, min(100, int(background_opacity))) * 255 // 100:02x}")
    y = start_y
    for line, box in zip(lines, line_boxes):
        width = box[2] - box[0]
        x = max(8, min(image.width - width - 8, round(image.width * max(0, min(100, int(x_percent))) / 100 - width / 2)))
        draw.text((x, y), line, font=font, fill=text_color, stroke_width=2, stroke_fill=outline_color)
        y += box[3] - box[1] + line_gap
    return image

def first_srt_caption(srt_text: str) -> str:
    """Return only the first timed caption as one compact line for the style preview."""
    normalized = normalize_srt_text(srt_text or "")
    for raw_block in re.split(r"\n\s*\n", normalized):
        lines = [line.strip() for line in raw_block.splitlines() if line.strip()]
        if len(lines) >= 3 and "-->" in lines[1]:
            return " ".join(lines[2:]).strip()
    return ""


def preview_caption_text(srt_text: str, max_chars: int = SUBTITLE_MAX_LINE_CHARS) -> str:
    """Return a compact one-line sample without changing the full SRT used for export."""
    caption = first_srt_caption(srt_text or "")
    if not caption:
        return ""
    lines: list[str] = []
    for source_line in caption.splitlines():
        lines.extend(wrap_subtitle_lines(source_line, max_chars))
    return lines[0] if lines else ""


def build_subtitle_preview_canvas(frame: Image.Image, output_width: int, output_height: int, background_blur: bool = False) -> Image.Image:
    """Mirror the final platform framing so preview X/Y percentages use the same canvas as ASS."""
    preview_width = min(480, max(240, int(output_width)))
    preview_height = max(180, round(preview_width * int(output_height) / max(1, int(output_width))))
    source = frame.convert("RGB")
    if not background_blur:
        return ImageOps.fit(source, (preview_width, preview_height), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    canvas = ImageOps.fit(source, (preview_width, preview_height), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5)).filter(ImageFilter.GaussianBlur(radius=10))
    foreground = ImageOps.contain(source, (preview_width, preview_height), method=Image.Resampling.LANCZOS)
    canvas.paste(foreground, ((preview_width - foreground.width) // 2, (preview_height - foreground.height) // 2))
    return canvas


def render_live_subtitle_browser_preview(frame: Image.Image, text: str, font_name: str, font_size: int, text_color: str, outline_color: str, background_mode: str, background_color: str, background_opacity: int, x_percent: int = 50, y_percent: int = 86, output_width: int | None = None, output_height: int | None = None) -> None:
    """Render a browser-side Unicode preview with the bundled Myanmar font embedded as base64."""
    image_buffer = BytesIO()
    frame.convert("RGB").save(image_buffer, format="PNG", optimize=True)
    image_b64 = base64.b64encode(image_buffer.getvalue()).decode("ascii")
    font_path = resolve_myanmar_font(font_name)
    font_face = ""
    if font_path and font_path.exists():
        font_b64 = base64.b64encode(font_path.read_bytes()).decode("ascii")
        font_face = f"@font-face{{font-family:'MgMyanmar';src:url(data:font/ttf;base64,{font_b64}) format('truetype');font-weight:400;font-style:normal;}}"
    clean_text = preview_caption_text(text, SUBTITLE_MAX_LINE_CHARS) if "-->" in (text or "") else (wrap_subtitle_lines(" ".join((text or "").split()), SUBTITLE_MAX_LINE_CHARS) or [""])[0]
    clean_text = html_lib.escape(clean_text[:120] or "မြန်မာစာတန်းထိုး စမ်းသပ်ခြင်း")
    x = max(0, min(100, int(x_percent)))
    y = max(0, min(100, int(y_percent)))
    opacity = max(0, min(100, int(background_opacity))) / 100
    if background_mode == "Solid background":
        background_css = f"background:{background_color};opacity:{opacity};"
        background_class = "has-bg"
    else:
        background_css = "background:transparent;"
        background_class = ""
    text_color_safe = html_lib.escape(text_color)
    outline_color_safe = html_lib.escape(outline_color)
    font_size_safe = max(1, min(96, int(font_size)))
    reference_width = max(1, int(output_width or frame.width))
    reference_height = max(1, int(output_height or frame.height))
    # Match the Blur Mask experience: show a generous direct video frame, then
    # place the live caption over that frame instead of a shallow style sample.
    component_height = max(320, min(560, int(frame.height * 0.84)))
    html = f"""
    <style>
      {font_face}
      html,body{{margin:0;background:transparent;overflow:hidden;}}
      .stage{{position:relative;width:100%;height:{component_height}px;overflow:hidden;background:#111;border:1px solid rgba(34,184,255,.34);border-radius:14px;box-sizing:border-box;}}
      .stage img{{width:100%;height:100%;object-fit:contain;display:block;}}
      .caption{{position:absolute;left:{x}%;top:{y}%;transform:translate(-50%,-50%);max-width:92%;padding:1px 2px;text-align:center;color:{text_color_safe};font-family:'MgMyanmar','Noto Sans Myanmar','Pyidaungsu',sans-serif;font-size:1px;line-height:1.2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-shadow:-1px -1px 0 {outline_color_safe},1px -1px 0 {outline_color_safe},-1px 1px 0 {outline_color_safe},1px 1px 0 {outline_color_safe};z-index:2;box-sizing:border-box;}}
      .caption.has-bg{{{background_css}border-radius:6px;}}
    </style>
    <div class="stage"><img src="data:image/png;base64,{image_b64}"/><div class="caption {background_class}">{clean_text}</div></div>
    <script>
      const stage = document.querySelector('.stage');
      const caption = document.querySelector('.caption');
      const scale = Math.min(stage.clientWidth / {reference_width}, stage.clientHeight / {reference_height});
      const fontSize = Math.max(1, {font_size_safe} * scale);
      const outline = Math.max(.35, 2 * scale);
      caption.style.fontSize = fontSize + 'px';
      caption.style.padding = Math.max(1, 8 * scale) + 'px ' + Math.max(1, 12 * scale) + 'px';
      caption.style.textShadow = `-${{outline}}px -${{outline}}px 0 {outline_color_safe}, ${{outline}}px -${{outline}}px 0 {outline_color_safe}, -${{outline}}px ${{outline}}px 0 {outline_color_safe}, ${{outline}}px ${{outline}}px 0 {outline_color_safe}`;
    </script>
    """
    st.components.v1.html(html, height=component_height, scrolling=False)


def render_final_xy_subtitle_test(frame: Image.Image, text: str, font_name: str, font_size: int, text_color: str, outline_color: str, background_mode: str, background_color: str, background_opacity: int, x_percent: int, y_percent: int, output_width: int, output_height: int) -> Image.Image:
    """Create a reliable visible Subtitle Test using the exact final export X/Y canvas."""
    canvas = build_subtitle_preview_canvas(frame, output_width, output_height, background_blur=False)
    preview_font_size = max(1, round(int(font_size) * canvas.width / max(1, int(output_width))))
    return render_live_subtitle_preview(
        canvas, text, font_name, preview_font_size, text_color, outline_color,
        background_mode, background_color, background_opacity, "Bottom", x_percent, y_percent,
    )


def render_ffmpeg_subtitle_test(video_path: Path, text: str, font_name: str, font_size: int, text_color: str, outline_color: str, background_mode: str, background_color: str, background_opacity: int, x_percent: int, y_percent: int, output_width: int, output_height: int, background_blur: bool = False, preserve_source_aspect: bool = False) -> Image.Image:
    """Render Subtitle Test with the same libass font, Unicode shaping, and ASS X/Y canvas as Final Video."""
    selected_font = resolve_myanmar_font(font_name)
    if not selected_font or not selected_font.is_file():
        raise RuntimeError("စာတန်းထိုး Font မတွေ့ပါ။")
    if "-->" in (text or ""):
        preview_text = first_srt_caption(text)
    else:
        preview_text = str(text or "မြန်မာစာတန်းထိုး စမ်းသပ်ခြင်း")
    preview_text = "\n".join(line.strip() for line in preview_text.splitlines() if line.strip())[:180]
    if not preview_text:
        preview_text = "မြန်မာစာတန်းထိုး စမ်းသပ်ခြင်း"
    dummy_srt = f"1\n00:00:00,000 --> 00:00:10,000\n{preview_text}\n"
    render_width, render_height = int(output_width), int(output_height)
    if preserve_source_aspect:
        source_size = get_video_dimensions(video_path)
        if not source_size:
            raise RuntimeError("မူရင်း Video အရွယ်အစား မဖတ်နိုင်သေးပါ။")
        source_width, source_height = source_size
        scale_ratio = min(1.0, 720 / max(1, source_width))
        render_width = max(2, int(source_width * scale_ratio) // 2 * 2)
        render_height = max(2, int(source_height * scale_ratio) // 2 * 2)
    with tempfile.TemporaryDirectory(prefix="mgkhant-subtitle-test-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        staged_font_dir = temp_dir / "fonts"
        staged_font_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(selected_font, staged_font_dir / selected_font.name)
        ass_path = temp_dir / "subtitle-test.ass"
        ass_path.write_text(
            build_unicode_ass(
                dummy_srt,
                resolved_font_family(font_name, selected_font),
                font_size,
                text_color,
                outline_color,
                background_mode,
                background_color,
                background_opacity,
                x_percent,
                y_percent,
                render_width,
                render_height,
            ),
            encoding="utf-8",
        )
        output_path = temp_dir / "subtitle-test.jpg"
        ass_filter = build_ass_subtitle_filter(ass_path, staged_font_dir)
        command = ["ffmpeg", "-y", "-ss", "0.35", "-i", str(video_path)]
        if background_blur and not preserve_source_aspect:
            filter_graph = (
                f"[0:v]split=2[mgk_bg][mgk_fg];"
                f"[mgk_bg]scale={render_width}:{render_height}:force_original_aspect_ratio=increase,"
                f"crop={render_width}:{render_height},boxblur=20:10,eq=brightness=0.03:saturation=1.06[mgk_blur];"
                f"[mgk_fg]scale={render_width}:{render_height}:force_original_aspect_ratio=decrease[mgk_fit];"
                f"[mgk_blur][mgk_fit]overlay=(W-w)/2:(H-h)/2[mgk_base];"
                f"[mgk_base]{ass_filter}[mgk_test]"
            )
            command.extend(["-filter_complex", filter_graph, "-map", "[mgk_test]"])
        else:
            video_filter_parts = [f"scale={render_width}:{render_height}"]
            if not preserve_source_aspect:
                video_filter_parts = [
                    f"scale={render_width}:{render_height}:force_original_aspect_ratio=decrease",
                    f"pad={render_width}:{render_height}:(ow-iw)/2:(oh-ih)/2:color=black",
                ]
            video_filter_parts.append(ass_filter)
            video_filter = ",".join(video_filter_parts)
            command.extend(["-vf", video_filter])
        result = subprocess.run(
            command + ["-frames:v", "1", "-q:v", "2", str(output_path)],
            capture_output=True,
            text=True,
            timeout=45,
        )
        if result.returncode != 0 or not output_path.exists():
            raise RuntimeError(result.stderr[-700:] or "Subtitle Test Preview မရသေးပါ။")
        with Image.open(output_path) as image:
            return image.convert("RGB").copy()


def render_export_progress_card(target, percent: int, current_step: str, subtitle_enabled: bool) -> None:
    """Show export activity in a compact, phone-friendly card while FFmpeg is working."""
    percent = max(0, min(100, int(percent)))
    stages = [
        ("Script ရေးနေသည်", 20),
        ("Voiceover ပြင်နေသည်", 55),
        (("စာတန်းထိုး ပြင်နေသည်" if subtitle_enabled else "Export settings ပြင်နေသည်"), 72),
        ("Video ဖိုင် ပေါင်းနေသည်", 96),
    ]
    stage_html = ""
    for label, threshold in stages:
        if percent >= threshold:
            marker, state = "✓", "done"
        elif current_step == label:
            marker, state = "◌", "active"
        else:
            marker, state = "○", ""
        stage_html += f"<div class='export-step {state}'><span>{marker}</span>{html_lib.escape(label)}</div>"
    current_step_safe = html_lib.escape(current_step)
    target.markdown(
        f"""
        <div class="export-card">
          <div class="export-card-head">
            <div class="export-activity">
              <span class="export-spinner"><i></i></span>
              <div><strong>Video ထုတ်နေပါတယ်<span class="export-dots"><i></i><i></i><i></i></span></strong><small>{current_step_safe}</small></div>
            </div>
            <b>{percent}%</b>
          </div>
          <div class="export-track"><i style="width:{percent}%"></i><span></span></div>
          <div class="export-steps">{stage_html}</div>
        </div>
        <style>
          .export-card{{margin:.45rem 0;padding:.68rem;border:1px solid rgba(100,207,255,.38);border-radius:14px;background:radial-gradient(circle at 8% 0,rgba(75,211,255,.13),transparent 36%),linear-gradient(135deg,rgba(27,62,93,.6),rgba(26,24,58,.78));box-shadow:inset 0 1px 0 rgba(255,255,255,.08),0 12px 26px rgba(0,0,0,.18);}}
          .export-card-head{{display:flex;justify-content:space-between;align-items:center;color:#edf7ff;font-size:.78rem;gap:.5rem;}}
          .export-activity{{display:flex;align-items:center;gap:.48rem;min-width:0;}}
          .export-activity strong{{display:block;color:#eff9ff;font-size:.78rem;line-height:1.15;white-space:nowrap;}}
          .export-activity small{{display:block;overflow:hidden;max-width:220px;color:#91cbe7;font-size:.62rem;line-height:1.2;white-space:nowrap;text-overflow:ellipsis;}}
          .export-card-head b{{flex:none;display:grid;place-items:center;min-width:2.2rem;height:2.2rem;border-radius:9px;color:#c7f8ff;font-size:.82rem;background:rgba(40,190,255,.13);border:1px solid rgba(100,222,255,.27);}}
          .export-spinner{{display:grid;place-items:center;width:1.72rem;height:1.72rem;border-radius:50%;background:rgba(60,196,255,.1);border:1px solid rgba(117,220,255,.25);}}
          .export-spinner i{{display:block;width:1rem;height:1rem;border:2px solid rgba(139,227,255,.25);border-top-color:#77e5ff;border-right-color:#9b7dff;border-radius:50%;animation:mgk-spin .72s linear infinite;}}
          .export-dots{{display:inline-flex;gap:2px;margin-left:3px;vertical-align:middle;}}
          .export-dots i{{width:3px;height:3px;border-radius:50%;background:#8eeeff;animation:mgk-dot 1s ease-in-out infinite;}}
          .export-dots i:nth-child(2){{animation-delay:.16s;}} .export-dots i:nth-child(3){{animation-delay:.32s;}}
          .export-track{{position:relative;height:7px;margin:.58rem 0 .58rem;border-radius:999px;overflow:hidden;background:rgba(2,7,18,.55);box-shadow:inset 0 1px 3px rgba(0,0,0,.48);}}
          .export-track i{{position:relative;z-index:1;display:block;height:100%;border-radius:999px;background:linear-gradient(90deg,#31d8ff,#6d9dff 52%,#a778ff);box-shadow:0 0 14px rgba(79,203,255,.7);transition:width .35s cubic-bezier(.23,1,.32,1);}}
          .export-track span{{position:absolute;inset:0;z-index:2;width:32%;background:linear-gradient(90deg,transparent,rgba(255,255,255,.55),transparent);animation:mgk-shimmer 1.4s ease-in-out infinite;}}
          .export-steps{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.28rem .5rem;}}
          .export-step{{min-width:0;color:#9aa9bd;font-size:.64rem;line-height:1.15;}}
          .export-step span{{display:inline-grid;place-items:center;width:15px;height:15px;margin-right:4px;border-radius:50%;border:1px solid currentColor;font-size:.62rem;}}
          .export-step.done{{color:#46d6ae;}} .export-step.active{{color:#89eaff;font-weight:700;}}
          .export-step.active span{{animation:mgk-pulse 1s ease-in-out infinite;}}
          @keyframes mgk-spin{{to{{transform:rotate(360deg)}}}} @keyframes mgk-shimmer{{0%{{transform:translateX(-140%)}}100%{{transform:translateX(420%)}}}} @keyframes mgk-dot{{0%,80%,100%{{transform:translateY(0);opacity:.35}}40%{{transform:translateY(-3px);opacity:1}}}} @keyframes mgk-pulse{{50%{{box-shadow:0 0 0 4px rgba(83,211,255,.12)}}}}
          @media (prefers-reduced-motion:reduce){{.export-spinner i,.export-dots i,.export-track span,.export-step.active span{{animation:none !important;}}}}
        </style>
        """,
        unsafe_allow_html=True,
    )


def sampled_frame_times(duration_seconds: int | None, count: int = 6) -> list[float]:
    if not duration_seconds or duration_seconds <= 1:
        return [0.0]
    usable = max(0.0, float(duration_seconds) - 0.5)
    return [round(usable * index / max(1, count - 1), 1) for index in range(count)]


def get_video_dimensions(video_path: Path) -> tuple[int, int] | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(video_path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        raw_dimensions = result.stdout.strip()
        if "x" not in raw_dimensions:
            return None
        width_text, height_text = raw_dimensions.split("x", 1)
        width, height = int(width_text), int(height_text)
        return (width, height) if width > 0 and height > 0 else None
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def draw_blur_selection(frame: Image.Image, boxes: list[tuple[int, int, int, int]], background_style: str) -> Image.Image:
    preview = frame.copy().convert("RGBA")
    draw = ImageDraw.Draw(preview, "RGBA")
    for index, (x, y, width, height) in enumerate(boxes, start=1):
        draw.rectangle((x, y, x + width, y + height), outline="#22b8ff", width=max(3, round(preview.width / 160)))
        if background_style == "Solid Box":
            draw.rectangle((x, y, x + width, y + height), fill=(20, 184, 255, 155))
        else:
            draw.rectangle((x, y, x + width, y + height), fill=(34, 184, 255, 55))
        draw.text((x + 8, y + 6), f"Blur Box {index}", fill=(255, 255, 255, 235))
    return preview.convert("RGB")


def apply_region_blur(video_path: Path, boxes: list[tuple[int, int, int, int]], blur_strength: int, background_style: str, solid_box_color: str = "#16B8FF") -> Path:
    output_path = Path(tempfile.mktemp(suffix="-blurred.mp4"))
    # FFmpeg boxblur rejects chroma radii >= 15; keep the UI value safe for all pixel formats.
    safe_blur_strength = min(12, max(0, int(blur_strength)))
    filter_parts = []
    previous = "0:v"
    for index, (x, y, width, height) in enumerate(boxes):
        width = max(2, width - (width % 2))
        height = max(2, height - (height % 2))
        x = max(0, x)
        y = max(0, y)
        base = f"base{index}"
        region = f"region{index}"
        masked = f"masked{index}"
        output = "vout" if index == len(boxes) - 1 else f"stage{index}"
        filter_parts.append(f"[{previous}]split=2[{base}][{region}]")
        if background_style == "Solid Box":
            safe_color = solid_box_color.strip().lstrip("#")[:6] or "16B8FF"
            filter_parts.append(f"color=c=0x{safe_color}@0.78:s={width}x{height}:d=1[solid{index}]")
            filter_parts.append(f"[{base}][solid{index}]overlay={x}:{y}[{output}]")
        elif background_style == "Transparent":
            filter_parts.append(f"[{region}]crop={width}:{height}:{x}:{y},boxblur={safe_blur_strength}:2[{masked}]")
            filter_parts.append(f"[{base}][{masked}]overlay={x}:{y}[{output}]")
        else:
            filter_parts.append(f"[{region}]crop={width}:{height}:{x}:{y},boxblur={safe_blur_strength}:2[{masked}]")
            filter_parts.append(f"[{base}][{masked}]overlay={x}:{y}[{output}]")
        previous = output
    filter_graph = ";".join(filter_parts)
    command = [
        "ffmpeg", "-y", "-i", str(video_path), "-filter_complex", filter_graph,
        "-map", "[vout]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-threads", "2",
        "-c:a", "copy", "-movflags", "+faststart", str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=900)
    if result.returncode != 0 or not output_path.exists():
        output_path.unlink(missing_ok=True)
        raise RuntimeError(result.stderr[-1200:])
    return output_path


def apply_copyright_edit(video_path: Path, mirror: bool, auto_zoom: bool, color_filter: bool, pitch_alter: bool) -> Path:
    """Render the one allowed Copyright Edit operation: optional horizontal flip."""
    output_path = Path(tempfile.mktemp(suffix="-copyright-edited.mp4"))
    video_filters = []
    if mirror:
        video_filters.append("hflip")
    # Other legacy arguments are intentionally ignored; Mirror is the only
    # Copyright Edit effect exposed by the current workflow.
    video_filter = ",".join(video_filters) if video_filters else "null"
    audio_filter = "asetrate=24960,aresample=24000,atempo=0.961538" if pitch_alter else "anull"
    command = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vf", video_filter, "-af", audio_filter,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-threads", "2",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=900)
    if result.returncode != 0 or not output_path.exists():
        output_path.unlink(missing_ok=True)
        raise RuntimeError(result.stderr[-1600:])
    return output_path


def build_atempo_filter(speed: float) -> str:
    # Video-time mode may need narration to stretch substantially. Chaining
    # atempo stages keeps the final voice and SRT aligned to the selected video.
    speed = max(0.0625, min(16.0, speed))
    factors = []
    while speed > 2.0:
        factors.append("atempo=2.0")
        speed /= 2.0
    while speed < 0.5:
        factors.append("atempo=0.5")
        speed /= 0.5
    factors.append(f"atempo={speed:.6f}")
    return ",".join(factors)


def adjust_pcm_audio_speed(audio_bytes: bytes, speed: float) -> bytes:
    """Apply the selected speed to raw 24 kHz mono PCM and return raw PCM."""
    if not audio_bytes or abs(float(speed) - 1.0) < 0.001:
        return audio_bytes
    input_path = Path(tempfile.mktemp(suffix=".pcm"))
    output_path = Path(tempfile.mktemp(suffix=".pcm"))
    input_path.write_bytes(audio_bytes)
    try:
        command = [
            "ffmpeg", "-y", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", str(input_path),
            "-af", build_atempo_filter(float(speed)), "-f", "s16le", "-ar", "24000", "-ac", "1", str(output_path),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
        if result.returncode != 0 or not output_path.exists():
            raise RuntimeError(result.stderr[-1200:])
        return output_path.read_bytes()
    finally:
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)


def subtitle_ass_color(hex_color: str, alpha: int = 0) -> str:
    color = hex_color.strip().lstrip("#")
    if len(color) != 6:
        color = "FFFFFF"
    red, green, blue = color[0:2], color[2:4], color[4:6]
    alpha = max(0, min(255, int(alpha)))
    return f"&H{alpha:02X}{blue}{green}{red}"


def subtitle_alignment(position: str) -> int:
    return {"Bottom": 2, "Center": 5, "Top": 8}.get(position, 2)


def subtitle_alignment_xy(x_percent: int, y_percent: int) -> int:
    x = max(0, min(100, int(x_percent)))
    y = max(0, min(100, int(y_percent)))
    horizontal = 1 if x < 34 else 2 if x < 67 else 3
    vertical = 7 if y < 34 else 5 if y < 67 else 2
    return {7: {1: 7, 2: 8, 3: 9}, 5: {1: 4, 2: 5, 3: 6}, 2: {1: 1, 2: 2, 3: 3}}[vertical][horizontal]


def build_subtitle_filter(srt_path: Path, font_name: str, font_size: int, text_color: str, outline_color: str, background_mode: str, background_color: str, background_opacity: int, position: str, fonts_dir: Path | None = None, x_percent: int = 50, y_percent: int = 86, original_width: int | None = None, original_height: int | None = None) -> str:
    back_alpha = 255 - round(max(0, min(100, int(background_opacity))) * 255 / 100)
    border_style = 3 if background_mode == "Solid background" else 1
    force_style = ",".join([
        f"FontName={font_name}", f"FontSize={max(1, min(96, int(font_size)))}",
        f"PrimaryColour={subtitle_ass_color(text_color)}", f"OutlineColour={subtitle_ass_color(outline_color)}",
        f"BackColour={subtitle_ass_color(background_color, back_alpha)}",
        f"BorderStyle={border_style}", "Outline=2", "Shadow=1", "WrapStyle=2",
        f"Alignment={subtitle_alignment_xy(x_percent, y_percent)}", "MarginV=42",
    ])
    # Use libass's explicit filename form and escape filter-special characters.
    escaped_srt = str(srt_path.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    escaped_fonts = str(fonts_dir.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'") if fonts_dir else ""
    fonts_option = f":fontsdir='{escaped_fonts}'" if escaped_fonts else ""
    original_size_option = f":original_size={int(original_width)}x{int(original_height)}" if original_width and original_height else ""
    # Burmese needs explicit UTF-8 decoding. This FFmpeg build exposes shaping
    # through libass/HarfBuzz internally but does not expose a `shaping` filter option.
    return f"subtitles=filename='{escaped_srt}':charenc=UTF-8{fonts_option}{original_size_option}:force_style='{force_style}'"


def srt_time_to_ass(value: str) -> str:
    """Convert a strict SRT timestamp into an ASS centisecond timestamp."""
    match = re.match(r"(\d+):(\d{2}):(\d{2}),(\d{3})$", value.strip())
    if not match:
        raise ValueError("Invalid SRT timestamp")
    hours, minutes, seconds, milliseconds = (int(part) for part in match.groups())
    return f"{hours}:{minutes:02d}:{seconds:02d}.{milliseconds // 10:02d}"


def build_unicode_ass(srt_text: str, font_name: str, font_size: int, text_color: str, outline_color: str, background_mode: str, background_color: str, background_opacity: int, x_percent: int, y_percent: int, width: int, height: int) -> str:
    """Create a fixed-resolution ASS track so libass never rescales Myanmar glyphs unexpectedly."""
    normalized = normalize_srt_text(srt_text)
    back_alpha = 255 - round(max(0, min(100, int(background_opacity))) * 255 / 100)
    border_style = 3 if background_mode == "Solid background" else 1
    outline = 0 if border_style == 3 else 2
    x = round(width * max(0, min(100, int(x_percent))) / 100)
    y = round(height * max(0, min(100, int(y_percent))) / 100)
    safe_family = str(font_name).replace(",", " ")
    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {int(width)}",
        f"PlayResY: {int(height)}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
        f"Style: Default,{safe_family},{max(1, min(96, int(font_size)))},{subtitle_ass_color(text_color)},{subtitle_ass_color(text_color)},{subtitle_ass_color(outline_color)},{subtitle_ass_color(background_color, back_alpha)},0,0,0,0,100,100,0,0,{border_style},{outline},1,5,20,20,20,1",
        "",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]
    events = []
    for raw_block in re.split(r"\n\s*\n", normalized):
        lines = [line.strip() for line in raw_block.splitlines() if line.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        try:
            start_text, end_text = [part.strip() for part in lines[1].split("-->", 1)]
            caption = "\\N".join(lines[2:]).replace("{", "\\{").replace("}", "\\}")
            events.append(f"Dialogue: 0,{srt_time_to_ass(start_text)},{srt_time_to_ass(end_text)},Default,,0,0,0,,{{\\an5\\pos({x},{y})}}{caption}")
        except (TypeError, ValueError):
            continue
    return "\n".join(header + events) + "\n"


def build_ass_subtitle_filter(ass_path: Path, fonts_dir: Path | None = None) -> str:
    escaped_ass = str(ass_path.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    escaped_fonts = str(fonts_dir.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'") if fonts_dir else ""
    fonts_option = f":fontsdir='{escaped_fonts}'" if escaped_fonts else ""
    return f"ass=filename='{escaped_ass}'{fonts_option}"


def calculate_sync_plan(video_duration: float, audio_duration: float, audio_speed: float, timing_basis: str = "Audio အချိန်") -> dict[str, float]:
    """Fit final video, narration, and subtitle cues to the selected timing basis."""
    requested_speed = max(0.5, min(2.0, float(audio_speed)))
    raw_audio = max(0.1, float(audio_duration))
    source_video = max(0.1, float(video_duration))
    requested_duration = raw_audio / requested_speed
    if timing_basis == "Video အချိန်":
        target = source_video
    else:
        # Audio timing follows the selected voice speed even when the narration
        # is longer than the source clip; video is slowed or sped up to match.
        target = requested_duration
    render_audio_speed = raw_audio / target
    video_speed = source_video / target
    return {
        "audio_speed": requested_speed,
        "render_audio_speed": render_audio_speed,
        "video_speed": video_speed,
        "adjusted_audio": target,
        "target": target,
    }


def render_dimensions(platform: str, quality_mode: str) -> tuple[int, int]:
    quality_dimensions = {
        "720": {
            "YouTube": (1280, 720),
            "TikTok": (720, 1280),
            "Facebook": (720, 720),
        },
        "1280": {
            "YouTube": (1920, 1080),
            "TikTok": (1280, 1920),
            "Facebook": (1280, 1280),
        },
    }
    return quality_dimensions.get(quality_mode, quality_dimensions["720"]).get(platform, (1280, 720))


def detect_content_crop(video_path: Path) -> str | None:
    """Detect substantial embedded black bars so Background Blur fills from real image content."""
    dimensions = get_video_dimensions(video_path)
    if not dimensions:
        return None
    source_width, source_height = dimensions
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-ss", "0.15", "-t", "2", "-i", str(video_path),
                "-vf", "cropdetect=24:16:0", "-an", "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=45,
        )
        candidates = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", result.stderr)
        if not candidates:
            return None
        crops = [(int(width), int(height), int(x), int(y)) for width, height, x, y in candidates]
        width, height, x, y = max(crops, key=lambda crop: crop[0] * crop[1])
        area_ratio = (width * height) / max(1, source_width * source_height)
        if width < source_width * 0.55 or height < source_height * 0.55 or area_ratio > 0.985:
            return None
        return f"crop={width}:{height}:{x}:{y}"
    except (OSError, subprocess.SubprocessError):
        return None


def sanitize_content_crop(crop_filter: str | None, source_dimensions: tuple[int, int] | None) -> str | None:
    """Keep cropdetect output inside the active video frame before adding it to FFmpeg."""
    if not crop_filter or not source_dimensions:
        return None
    match = re.fullmatch(r"crop=(\d+):(\d+):(\d+):(\d+)", crop_filter.strip())
    if not match:
        return None
    source_width, source_height = (max(2, int(value)) for value in source_dimensions)
    width, height, x, y = (int(value) for value in match.groups())
    width = max(2, min(width, source_width))
    height = max(2, min(height, source_height))
    # yuv420 crop dimensions and offsets must be even on the deployed FFmpeg build.
    width -= width % 2
    height -= height % 2
    x = max(0, min(x, source_width - width))
    y = max(0, min(y, source_height - height))
    x -= x % 2
    y -= y % 2
    if width < 2 or height < 2:
        return None
    if (width * height) / max(1, source_width * source_height) > 0.985:
        return None
    return f"crop={width}:{height}:{x}:{y}"


def merge_audio_video(video_path: Path, audio_bytes: bytes, platform: str, speed: float = 1.0, subtitle_srt: str = "", subtitle_font: str = "Noto Sans Myanmar", subtitle_size: int = 34, subtitle_text_color: str = "#FFFFFF", subtitle_outline_color: str = "#000000", subtitle_background_mode: str = "Transparent", subtitle_background_color: str = "#000000", subtitle_background_opacity: int = 55, subtitle_position: str = "Bottom", subtitle_x: int = 50, subtitle_y: int = 86, effect_mirror: bool = False, effect_auto_zoom: bool = False, effect_color_filter: bool = False, effect_pitch_alter: bool = False, effect_background_blur: bool = False, effect_freeze_bypass: bool = False, logo_path: str | Path | None = None, logo_position: str = "Top right", logo_motion: str = "Static", moving_logo_text: str = "", text_position: str = "Bottom center", quality_mode: str = "720", timing_basis: str = "Audio အချိန်", subtitle_is_final_timeline: bool = False) -> bytes:
    audio_handle = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    audio_path = Path(audio_handle.name)
    audio_handle.close()
    srt_path: Path | None = None
    ass_path: Path | None = None
    burnin_font_dir: Path | None = None
    moving_text_path: Path | None = None
    if subtitle_srt.strip():
        srt_handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", delete=False, suffix=".srt")
        try:
            srt_handle.write(normalize_srt_text(subtitle_srt))
            srt_handle.flush()
            os.fsync(srt_handle.fileno())
        finally:
            srt_handle.close()
        srt_path = Path(srt_handle.name).resolve()
        if not srt_path.exists() or srt_path.stat().st_size == 0:
            raise RuntimeError("Subtitle SRT temporary file ကို မဖန်တီးနိုင်ပါ။")
    output_path = Path(tempfile.mktemp(suffix=".mp4"))
    wav_bytes = pcm_to_wav(audio_bytes)
    audio_path.write_bytes(wav_bytes)
    video_duration = get_video_duration(video_path)
    if not video_duration:
        raise RuntimeError("Original video duration ကို မဖတ်နိုင်ပါ။")
    audio_duration = max(0.1, len(audio_bytes) / (24000 * 2))
    sync_plan = calculate_sync_plan(video_duration, audio_duration, speed, timing_basis)
    audio_speed = sync_plan["render_audio_speed"]
    auto_video_speed = sync_plan["video_speed"]
    target_duration = sync_plan["target"]
    if srt_path is not None and not subtitle_is_final_timeline:
        # Burn every subtitle cue on the selected final export timeline.
        srt_path.write_text(scale_srt_to_duration(subtitle_srt, target_duration), encoding="utf-8", newline="\n")
    # User controls audio speed; video speed is derived automatically to match narration duration.
    audio_filter = []
    if effect_pitch_alter:
        # Small pitch shift with duration compensation; avoids changing narration length.
        audio_filter.extend(["asetrate=24960", "aresample=24000", "atempo=0.961538"])
    audio_filter.extend([build_atempo_filter(audio_speed), "aresample=async=1:first_pts=0"])
    output_width, output_height = render_dimensions(platform, quality_mode)
    video_parts = [f"setpts=PTS/{auto_video_speed:.6f}"]
    if effect_mirror:
        video_parts.append("hflip")
    if effect_auto_zoom:
        video_parts.append("scale=iw*1.08:ih*1.08,crop=iw/1.08:ih/1.08")
    if effect_freeze_bypass:
        # Brief opening freeze followed by a gentle crop zoom. This is applied
        # only in the final render and is bounded by the existing -t duration.
        video_parts.append("tpad=start_mode=clone:start_duration=0.35")
        video_parts.append("scale=iw*1.035:ih*1.035,crop=iw/1.035:ih/1.035")
    if effect_color_filter:
        video_parts.append("eq=contrast=1.04:brightness=0.02:saturation=1.12")
    complex_filter = bool(effect_background_blur)
    active_label = ""
    if complex_filter:
        content_crop = sanitize_content_crop(detect_content_crop(video_path), get_video_dimensions(video_path))
        pre_fit_filter = ",".join(video_parts + ([content_crop] if content_crop else []))
        video_filter = (
            f"[0:v]{pre_fit_filter},split=2[mgk_bg][mgk_fg];"
            f"[mgk_bg]scale={output_width}:{output_height}:force_original_aspect_ratio=increase,crop={output_width}:{output_height},boxblur=20:10,eq=brightness=0.03:saturation=1.06[mgk_blur];"
            f"[mgk_fg]scale={output_width}:{output_height}:force_original_aspect_ratio=decrease[mgk_fit];"
            f"[mgk_blur][mgk_fit]overlay=(W-w)/2:(H-h)/2[mgk_base]"
        )
        active_label = "mgk_base"
    else:
        # Keep the complete source image visible whenever Background Blur is
        # off. With the option on, the branch above uses a blurred fill instead.
        video_parts.append(f"scale={output_width}:{output_height}:force_original_aspect_ratio=decrease")
        video_parts.append(f"pad={output_width}:{output_height}:(ow-iw)/2:(oh-ih)/2:color=black")
        video_filter = ",".join(video_parts)
    if srt_path is not None:
        provided_font_dir = Path(__file__).resolve().parent / "fonts"
        myanmar_font_path = resolve_myanmar_font(subtitle_font)
        if not myanmar_font_path or not myanmar_font_path.exists():
            raise RuntimeError("Unicode Myanmar font မတွေ့ပါ။ packages.txt နဲ့ fonts/ folder ကို စစ်ပါ။")
        subtitle_font_name = resolved_font_family(subtitle_font, myanmar_font_path)
        burnin_font_dir = Path(tempfile.mkdtemp(prefix="mgkhant-fonts-"))
        shutil.copy2(myanmar_font_path, burnin_font_dir / myanmar_font_path.name)
        subtitle_fonts_dir = burnin_font_dir
        ass_handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", delete=False, suffix=".ass")
        try:
            ass_handle.write(build_unicode_ass(srt_path.read_text(encoding="utf-8"), subtitle_font_name, subtitle_size, subtitle_text_color, subtitle_outline_color, subtitle_background_mode, subtitle_background_color, subtitle_background_opacity, subtitle_x, subtitle_y, output_width, output_height))
            ass_handle.flush()
            os.fsync(ass_handle.fileno())
        finally:
            ass_handle.close()
        ass_path = Path(ass_handle.name).resolve()
        subtitle_filter = build_ass_subtitle_filter(ass_path, subtitle_fonts_dir)
        if complex_filter:
            video_filter += f";[{active_label}]{subtitle_filter}[mgk_sub]"
            active_label = "mgk_sub"
        else:
            video_filter += "," + subtitle_filter
    if moving_logo_text.strip():
        text_handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", delete=False, suffix=".txt")
        text_handle.write(moving_logo_text.strip())
        text_handle.close()
        moving_text_path = Path(text_handle.name).resolve()
        logo_font = resolve_myanmar_font("Noto Sans Myanmar") or resolve_myanmar_font("Pyidaungsu Bold")
        if logo_font:
            escaped_text = str(moving_text_path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
            escaped_logo_font = str(logo_font.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
            text_x = "24" if logo_motion == "Left static" else "w-tw-24"
            text_y = "h-th-36"
            if logo_motion == "Full-screen movement":
                moving_x = "mod(t*180\\,w+tw)-tw"
                moving_y = "mod(t*120\\,h+th)-th"
            else:
                moving_x = text_x
                moving_y = text_y
            drawtext_filter = f"drawtext=fontfile='{escaped_logo_font}':textfile='{escaped_text}':fontcolor=white:bordercolor=black:borderw=2:fontsize=34:x={moving_x}:y={moving_y}"
            if complex_filter:
                video_filter += f";[{active_label}]{drawtext_filter}[mgk_text]"
                active_label = "mgk_text"
            else:
                video_filter += f",{drawtext_filter}"
    logo_file = Path(logo_path).resolve() if logo_path else None
    if logo_file and not logo_file.is_file():
        raise RuntimeError("Logo ဖိုင်ကို မတွေ့ပါ။ Logo ကို ပြန်တင်ပါ။")
    command = ["ffmpeg", "-y", "-i", str(video_path), "-i", str(audio_path)]
    if logo_file:
        logo_x = {"Left": "24", "Right": "W-w-24"}.get(logo_position, "W-w-24")
        logo_y = "24"
        # The uploaded logo image always stays fixed at the selected side.
        logo_badge_size = max(44, min(160, round(min(output_width, output_height) * 0.12)))
        logo_filter = f"format=rgba,scale={logo_badge_size}:{logo_badge_size}:force_original_aspect_ratio=decrease"
        if complex_filter:
            graph = f"{video_filter};[2:v]{logo_filter}[logo];[{active_label}][logo]overlay=x={logo_x}:y={logo_y}:shortest=1[vout]"
        else:
            graph = f"[0:v]{video_filter}[base];[2:v]{logo_filter}[logo];[base][logo]overlay=x={logo_x}:y={logo_y}:shortest=1[vout]"
        command.extend(["-loop", "1", "-i", str(logo_file), "-filter_complex", graph, "-map", "[vout]"])
    else:
        if complex_filter:
            command.extend(["-filter_complex", video_filter, "-map", f"[{active_label}]"])
        else:
            command.extend(["-map", "0:v:0", "-vf", video_filter])
    command.extend([
        "-map", "1:a:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-threads", "2",
        "-c:a", "aac", "-b:a", "128k", "-af", ",".join(audio_filter),
        "-t", f"{target_duration:.3f}", "-movflags", "+faststart", str(output_path),
    ])
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=1200)
        if result.returncode != 0 or not output_path.exists():
            subtitle_open_failed = srt_path is not None and "Unable to open" in (result.stderr or "") and ".srt" in (result.stderr or "")
            if subtitle_open_failed:
                # Subtitle Toggle is ON: never silently return a subtitle-free MP4.
                # The caller must see the real subtitle error and can retry after deployment refresh.
                raise RuntimeError("SRT ကို Final Video ထဲ မပေါင်းနိုင်ပါ။ SRT လမ်းကြောင်း/Font ကို စစ်ပါ။\n" + (result.stderr or "")[-1200:])
            raise RuntimeError((result.stderr or "FFmpeg failed")[-1400:])
        return output_path.read_bytes()
    finally:
        audio_path.unlink(missing_ok=True)
        if srt_path is not None:
            srt_path.unlink(missing_ok=True)
        if ass_path is not None:
            ass_path.unlink(missing_ok=True)
        if moving_text_path is not None:
            moving_text_path.unlink(missing_ok=True)
        if burnin_font_dir is not None:
            shutil.rmtree(burnin_font_dir, ignore_errors=True)
        output_path.unlink(missing_ok=True)


def mix_final_audio_layers(final_video: bytes, source_video_path: Path, original_audio_mode: str, music_path: str | Path | None = None, music_volume: int = 12, video_speed: float = 1.0) -> bytes:
    """Keep the finished video stream and replace its audio with the requested final mix.

    The first track is always the generated Burmese voiceover from ``final_video``.
    ``တိုက်ခိုက်သံထား`` adds a deliberately quiet filtered copy of the source
    soundtrack so action ambience can remain without overpowering narration.
    This is a low-volume mix, not a claim of perfect dialogue stem separation.
    """
    keep_action_sound = original_audio_mode == "တိုက်ခိုက်သံထား" and source_has_audio(source_video_path)
    music_file = Path(music_path).resolve() if music_path else None
    if not keep_action_sound and not (music_file and music_file.is_file()):
        return final_video

    final_path = Path(tempfile.mktemp(suffix="-voice.mp4"))
    output_path = Path(tempfile.mktemp(suffix="-mixed.mp4"))
    final_path.write_bytes(final_video)
    try:
        duration = get_video_duration(final_path) or get_video_duration(source_video_path) or 1.0
        command = ["ffmpeg", "-y", "-i", str(final_path)]
        input_index = 1
        action_index: int | None = None
        music_index: int | None = None
        if keep_action_sound:
            command.extend(["-i", str(source_video_path)])
            action_index = input_index
            input_index += 1
        if music_file and music_file.is_file():
            command.extend(["-stream_loop", "-1", "-i", str(music_file)])
            music_index = input_index

        audio_parts = ["[0:a]aresample=async=1:first_pts=0[voice]"]
        mix_labels = ["[voice]"]
        if action_index is not None:
            # Keep source ambience low. atempo follows the final video speed so
            # action sound remains aligned with speed-fitted video frames.
            action_tempo = build_atempo_filter(max(0.0625, min(16.0, float(video_speed))))
            audio_parts.append(
                f"[{action_index}:a]highpass=f=180,lowpass=f=8500,{action_tempo},volume=0.22,"
                f"atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[action]"
            )
            mix_labels.append("[action]")
        if music_index is not None:
            volume = max(0, min(35, int(music_volume))) / 100
            audio_parts.append(
                f"[{music_index}:a]volume={volume:.3f},atrim=duration={duration:.3f},"
                "asetpts=PTS-STARTPTS[music]"
            )
            mix_labels.append("[music]")
        audio_parts.append(
            "".join(mix_labels) + f"amix=inputs={len(mix_labels)}:duration=first:normalize=0,alimiter=limit=0.95[aout]"
        )
        command.extend([
            "-filter_complex", ";".join(audio_parts), "-map", "0:v:0", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-t", f"{duration:.3f}",
            "-movflags", "+faststart", str(output_path),
        ])
        result = subprocess.run(command, capture_output=True, text=True, timeout=1200)
        if result.returncode != 0 or not output_path.exists():
            raise RuntimeError((result.stderr or "Audio mix failed")[-1400:])
        return output_path.read_bytes()
    finally:
        final_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)


def source_has_audio(video_path: Path) -> bool:
    """Return whether a source video exposes at least one audio stream."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=index", "-of", "csv=p=0", str(video_path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def apply_cinematic_theme():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');
        :root { --mgk-gold:#f4c95d; --mgk-coral:#ff6b6b; --mgk-violet:#7c5cff; --mgk-navy:#0b1020; --mgk-ink:#f8f6ef; --mgk-muted:#aeb5c8; --mgk-panel:rgba(18,25,46,.86); --coral:var(--mgk-coral); --violet:var(--mgk-violet); --ink:var(--mgk-ink); --muted:var(--mgk-muted); --panel:var(--mgk-panel); }
        .stApp { background:radial-gradient(circle at 8% 0%,rgba(124,92,255,.24),transparent 31%),radial-gradient(circle at 92% 12%,rgba(244,201,93,.12),transparent 24%),linear-gradient(160deg,#070b16 0%,#0b1020 52%,#151026 100%); color:var(--mgk-ink); font-family:'DM Sans','Noto Sans Myanmar',sans-serif; }
        .stApp::before { content:''; position:fixed; inset:0; pointer-events:none; opacity:.12; background-image:linear-gradient(rgba(255,255,255,.04) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.04) 1px,transparent 1px); background-size:48px 48px; mask-image:linear-gradient(to bottom,black,transparent 78%); }
        h1,h2,h3 { font-family:'Space Grotesk','Noto Sans Myanmar',sans-serif !important; letter-spacing:-.04em; }
        h1 { font-size:clamp(2.2rem,6vw,4.8rem) !important; background:linear-gradient(100deg,#fff 18%,#f4c95d 54%,#9c8bff 90%); -webkit-background-clip:text; color:transparent; margin-bottom:.2rem !important; }
        h2 { color:#fff !important; }
        [data-testid='stHeader'] { background:rgba(8,9,16,.72); }
        [data-testid='stSidebar'] { background:linear-gradient(180deg,rgba(20,22,35,.96),rgba(11,12,20,.98)); border-right:1px solid rgba(255,255,255,.09); }
        [data-testid='stSidebar'] h2 { font-size:1.3rem !important; }
        [data-testid='stExpander'] { background:linear-gradient(145deg,rgba(41,37,65,.72),rgba(19,21,32,.72)); border:1px solid rgba(255,255,255,.12); border-radius:20px; box-shadow:0 20px 60px rgba(0,0,0,.22); }
        [data-testid='stFileUploader'] { background:linear-gradient(145deg,rgba(28,39,70,.92),rgba(18,25,46,.9)); border:1px solid rgba(244,201,93,.5); border-radius:24px; min-height:190px; padding:30px 18px; box-shadow:0 18px 54px rgba(0,0,0,.3),0 0 0 1px rgba(124,92,255,.16) inset; }
        [data-testid='stFileUploader'] section { background:transparent; border:0; }
        [data-testid='stFileUploaderDropzone'] { background:linear-gradient(135deg,rgba(244,201,93,.1),rgba(124,92,255,.12)); border:1px dashed rgba(244,201,93,.45); border-radius:18px; min-height:125px; }
        .stButton > button { width:100%; border:1px solid rgba(244,201,93,.34); border-radius:12px; padding:.72rem 1rem; color:#101522 !important; background:linear-gradient(135deg,#f4c95d,#ff8b6b 52%,#7c5cff); box-shadow:0 10px 28px rgba(124,92,255,.22); font-weight:800; transition:transform .18s ease,box-shadow .18s ease; }
        [data-testid='stPopover'] > button, [data-testid='stFileUploaderDropzone'] button { color:#fff !important; background:linear-gradient(135deg,#ff4f67,#844cff) !important; border:1px solid rgba(255,255,255,.25) !important; font-weight:700 !important; }
        [data-testid='stFileUploaderDropzone'] small, [data-testid='stFileUploaderDropzone'] span, [data-testid='stFileUploaderDropzone'] p, label, .stCaption, [data-testid='stCaptionContainer'] { color:#d8d9e5 !important; }
        .stButton > button:hover { transform:translateY(-2px); box-shadow:0 14px 34px rgba(244,201,93,.28); border-color:rgba(244,201,93,.7); }
        .stButton > button:active { transform:scale(.98); }
        .stDownloadButton > button { width:100%; border-radius:12px; color:#ffdce0; background:rgba(255,79,103,.12); border:1px solid rgba(255,79,103,.38); }
        .stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb='select'] > div, .stNumberInput input { color:#fff !important; background:rgba(8,9,16,.72) !important; border:1px solid rgba(255,255,255,.13) !important; border-radius:11px !important; }
        /* Dropdowns are selection-only on mobile: keep their hidden search input from summoning the keyboard. */
        .stSelectbox [data-baseweb='select'] input { pointer-events:none !important; caret-color:transparent !important; user-select:none !important; -webkit-user-select:none !important; }
        .stSelectbox [data-baseweb='select'] [role='combobox'] { cursor:pointer !important; }
        .stSelectbox [data-baseweb='select'] { touch-action:manipulation; }
        @media (max-width:700px) { .stSelectbox [data-baseweb='select'] input:focus { outline:none !important; } }
        .stRadio input, .stButton button { -webkit-tap-highlight-color:transparent; }
        .stTextArea textarea:focus, .stTextInput input:focus { border-color:var(--coral) !important; box-shadow:0 0 0 1px var(--coral) !important; }
        @media (max-width:700px) {
            [data-testid='stAppViewContainer'] .main .block-container { max-width:100% !important; padding:.45rem .55rem 1.2rem !important; }
            h1 { font-size:1.55rem !important; line-height:1.05 !important; }
            h2 { font-size:1.18rem !important; line-height:1.12 !important; }
            h3 { font-size:.98rem !important; line-height:1.15 !important; }
            p, label, [data-testid='stCaptionContainer'] { font-size:.72rem !important; line-height:1.25 !important; }
            div[data-testid='stHorizontalBlock'] { flex-direction:column !important; flex-wrap:wrap !important; gap:.35rem !important; align-items:stretch !important; }
            div[data-testid='stHorizontalBlock'] > div[data-testid='column'] { min-width:0 !important; width:100% !important; flex:1 1 100% !important; padding-left:0 !important; padding-right:0 !important; }
            div[data-testid='stHorizontalBlock']:has([data-testid='stVerticalBlockBorderWrapper']) { flex-direction:row !important; flex-wrap:nowrap !important; align-items:stretch !important; }
            div[data-testid='stHorizontalBlock']:has([data-testid='stVerticalBlockBorderWrapper']) > div[data-testid='column'] { width:0 !important; flex:1 1 0 !important; padding-left:.12rem !important; padding-right:.12rem !important; }
            div[data-testid='stHorizontalBlock'] .stButton > button { white-space:normal !important; overflow:hidden !important; text-overflow:ellipsis !important; font-size:.67rem !important; padding:.28rem .22rem !important; }
            .stSelectbox, .stTextInput, .stTextArea, .stSlider, .stColorPicker, .stAlert { margin-bottom:.22rem !important; }
            [data-testid='stWidgetLabel'] { font-size:.72rem !important; line-height:1.1 !important; margin-bottom:.12rem !important; }
            .stSelectbox div[data-baseweb='select'] > div { min-height:2rem !important; padding-top:.12rem !important; padding-bottom:.12rem !important; }
            .stButton > button, .stDownloadButton > button { min-height:1.78rem !important; padding:.24rem .38rem !important; font-size:.68rem !important; line-height:1.08 !important; border-radius:8px !important; }
            [data-testid='stVerticalBlockBorderWrapper'] { padding:.45rem !important; border-radius:10px !important; }
            [data-testid='stFileUploader'] { min-height:120px !important; padding:12px 10px !important; border-radius:14px !important; }
            [data-testid='stFileUploaderDropzone'] { min-height:80px !important; border-radius:11px !important; }

            .stTextArea textarea { min-height:7rem !important; padding:.5rem !important; }
            [data-testid='stVideo'] video { max-height:38vh !important; object-fit:contain !important; }
            [data-testid='stAudio'] audio { height:34px !important; }
            .recap-hero { padding:12px 14px !important; margin:4px 0 10px !important; border-radius:14px !important; }
            .stAlert { padding:.42rem .58rem !important; font-size:.76rem !important; }

            h1, h2, h3 { margin-top:.45rem !important; margin-bottom:.28rem !important; }
        }
        [data-testid='stAlert'] { border-radius:14px; border:1px solid rgba(255,255,255,.12); background:rgba(255,255,255,.06); }
        [data-testid='stMetric'] { background:rgba(255,255,255,.055); border:1px solid rgba(255,255,255,.09); border-radius:15px; padding:12px; }
        .recap-hero { display:flex; align-items:center; justify-content:space-between; gap:20px; padding:26px 28px; margin:8px 0 22px; border:1px solid rgba(255,255,255,.11); border-radius:24px; background:linear-gradient(120deg,rgba(49,37,83,.84),rgba(28,23,42,.64) 52%,rgba(75,27,41,.42)); box-shadow:0 24px 70px rgba(0,0,0,.28); position:relative; overflow:hidden; }
        .recap-hero::after { content:'✦  REC  /  01'; position:absolute; right:24px; bottom:14px; color:rgba(255,255,255,.25); letter-spacing:.18em; font-size:.7rem; }
        .hero-kicker { color:#ff8b9b; text-transform:uppercase; letter-spacing:.2em; font-size:.7rem; font-weight:700; margin-bottom:8px; }
        .hero-copy { color:#b9b9ca; margin:0; max-width:580px; }
        .hero-orb { width:74px; height:74px; flex:none; display:grid; place-items:center; border-radius:23px; color:#fff; font-size:2rem; background:linear-gradient(145deg,var(--coral),var(--violet)); box-shadow:0 0 45px rgba(255,79,103,.36); transform:rotate(-8deg); }
        .section-label { color:#ff8b9b; font-weight:700; letter-spacing:.14em; font-size:.72rem; text-transform:uppercase; margin:18px 0 8px; }
        .video-meta-strip { margin:2px 0 0; padding:2px 8px 0; border-top:1px solid rgba(255,255,255,.12); color:#aeb3c8; }
        .effect-wizard-card { display:flex; align-items:center; justify-content:space-between; gap:12px; margin:10px 0 8px; padding:14px 16px; border:1px solid rgba(244,201,93,.36); border-radius:15px; background:linear-gradient(135deg,rgba(255,79,103,.16),rgba(124,92,255,.18)); }
        .effect-wizard-card span { color:#f4c95d; font-size:.7rem; font-weight:800; letter-spacing:.08em; text-transform:uppercase; }
        .effect-wizard-card strong { color:#fff; font-size:.95rem; }
        .video-meta-strip [data-testid='stVerticalBlock'] { gap:0 !important; }
        .video-meta-strip [data-testid='stCaptionContainer'] { margin:0 !important; padding:0 !important; font-size:.68rem !important; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
        [data-testid='stRadio'] > div[role='radiogroup'] { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:.28rem; padding:2px 0 5px; }
        [data-testid='stRadio'] > div[role='radiogroup']::-webkit-scrollbar { display:none; }
        [data-testid='stRadio'] label { min-width:0; min-height:2.35rem; aspect-ratio:1 / 1; display:grid; place-items:center; text-align:center; padding:3px 4px !important; border-radius:10px; background:rgba(255,255,255,.055); border:1px solid rgba(255,255,255,.08); font-size:.65rem !important; line-height:1.05 !important; overflow:hidden; }
        @media (max-width:700px) { .recap-hero { padding:20px; } .hero-orb { width:54px; height:54px; border-radius:17px; font-size:1.4rem; } .recap-hero::after { display:none; } [data-testid='stFileUploader'] { min-height:88px; padding:8px 8px; border-radius:14px; } [data-testid='stFileUploaderDropzone'] { min-height:56px; border-radius:11px; } .video-meta-strip { padding-left:2px; padding-right:2px; } .stButton > button, .stDownloadButton > button { min-height:40px !important; padding:0.35rem 0.65rem !important; font-size:0.78rem !important; } .stSelectbox, .stTextInput, .stSlider { margin-bottom:0.25rem !important; } }
        @media (min-width:701px) { [data-testid='stRadio'] > div[role='radiogroup'] { grid-template-columns:repeat(3,minmax(0,1fr)); } }
        @media (max-width:700px) {
            [data-testid='stAppViewContainer'] .main .block-container { padding:.28rem .38rem .7rem !important; }
            h1 { font-size:1.2rem !important; } h2 { font-size:1rem !important; } h3 { font-size:.86rem !important; }
            p, label, [data-testid='stCaptionContainer'] { font-size:.62rem !important; line-height:1.12 !important; }
            [data-testid='stFileUploader'] { min-height:64px !important; padding:6px !important; border-radius:10px !important; }
            [data-testid='stFileUploaderDropzone'] { min-height:44px !important; border-radius:8px !important; }
            [data-testid='stVideo'] video { max-height:26vh !important; }
            [data-testid='stVerticalBlockBorderWrapper'] { padding:.26rem !important; border-radius:8px !important; }
            .stButton > button, .stDownloadButton > button { min-height:1.62rem !important; padding:.18rem .28rem !important; font-size:.62rem !important; border-radius:7px !important; }
            .stSelectbox div[data-baseweb='select'] > div { min-height:1.7rem !important; }
            .stSlider, .stColorPicker, .stSelectbox, .stTextInput, .stTextArea, .stAlert { margin-bottom:.1rem !important; }
            [data-testid='stWidgetLabel'] { font-size:.62rem !important; margin-bottom:.05rem !important; }
            .stTextArea textarea { min-height:4.25rem !important; padding:.28rem !important; }
            hr { margin:.45rem 0 !important; }
        }
        /* Mg Khant dark refinement: brighter hierarchy on deep navy, without increasing mobile height. */
        [data-testid='stVerticalBlockBorderWrapper'] { background:linear-gradient(145deg,rgba(28,35,62,.82),rgba(12,16,31,.93)) !important; border:1px solid rgba(153,174,255,.15) !important; box-shadow:0 12px 34px rgba(0,0,0,.2),inset 0 1px 0 rgba(255,255,255,.035) !important; }
        [data-testid='stVideo'] { overflow:hidden; border:1px solid rgba(104,166,255,.34); border-radius:14px; background:#050813; box-shadow:0 12px 28px rgba(0,0,0,.32); }
        [data-testid='stVideo'] video { background:#050813; }
        .final-preview-note { color:#a9c4eb; font-size:.68rem; letter-spacing:.04em; text-align:center; margin:0 0 .28rem; }
        .final-preview-shell [data-testid='stVideo'] { border-color:rgba(244,201,93,.42); box-shadow:0 12px 28px rgba(0,0,0,.34),0 0 0 1px rgba(124,92,255,.12); }
        .workflow-fixed-preview { margin-top:.2rem; padding:.28rem; border-radius:15px; background:linear-gradient(135deg,rgba(75,92,162,.16),rgba(10,14,29,.25)); border:1px solid rgba(111,142,255,.13); }
        .video-meta-strip { border-color:rgba(154,173,255,.16) !important; color:#abb8d4 !important; }
        [data-testid='stStatusWidget'] { border:1px solid rgba(83,196,255,.28) !important; border-radius:12px !important; background:rgba(14,29,50,.74) !important; }
        [data-testid='stProgress'] > div > div { background:linear-gradient(90deg,#2bd2ff,#8b6cff) !important; }
        .stSelectbox div[data-baseweb='select'] > div, .stTextInput input, .stTextArea textarea, .stNumberInput input { background:linear-gradient(135deg,rgba(22,28,48,.94),rgba(16,19,35,.94)) !important; border-color:rgba(145,164,235,.2) !important; box-shadow:inset 0 1px 0 rgba(255,255,255,.025); }
        .stSelectbox div[data-baseweb='select'] > div:hover, .stTextInput input:hover, .stTextArea textarea:hover { border-color:rgba(111,180,255,.52) !important; }
        .stDownloadButton > button { color:#dcefff !important; background:linear-gradient(135deg,rgba(42,117,172,.42),rgba(80,60,145,.38)) !important; border-color:rgba(99,196,255,.48) !important; box-shadow:none !important; }
        .stDownloadButton > button:hover { background:linear-gradient(135deg,rgba(50,149,215,.62),rgba(111,82,194,.56)) !important; }
        .final-result-title { color:#dff6ff; font-size:.92rem; font-weight:800; letter-spacing:.01em; }
        .final-result-copy { color:#9fb7d9; margin:.12rem 0 .45rem; font-size:.7rem; }
        .mgk-app-brand { display:flex; align-items:center; gap:.46rem; min-height:2.25rem; color:#f5f7ff; font-family:'Space Grotesk','Noto Sans Myanmar',sans-serif; font-size:1.05rem; font-weight:800; letter-spacing:-.03em; }
        .mgk-app-brand span { display:grid; place-items:center; width:1.65rem; height:1.65rem; border-radius:.45rem; color:#d5a6ff; background:linear-gradient(135deg,rgba(155,77,255,.36),rgba(64,206,255,.14)); border:1px solid rgba(190,131,255,.42); }
        .mgk-card-head { display:flex; align-items:center; gap:.4rem; color:#f7f9ff; font-family:'Space Grotesk','Noto Sans Myanmar',sans-serif; font-size:1.02rem; font-weight:800; line-height:1.25; margin:0 0 .48rem; }
        .mgk-card-head b { color:#d5a6ff; font-size:1.13rem; }
        .mgk-card-subhead { color:#dce8ff; font-size:.72rem; font-weight:800; margin:.46rem 0 .18rem; }
        [data-testid='stVerticalBlockBorderWrapper']:has(.mgk-card-head) { padding:.72rem !important; border-radius:13px !important; border-color:rgba(135,160,230,.26) !important; background:linear-gradient(145deg,rgba(15,25,42,.96),rgba(9,15,28,.98)) !important; box-shadow:0 10px 26px rgba(0,0,0,.2) !important; }
        .mgk-three-cell { display:none; }
        html, body, [data-testid='stAppViewContainer'], [data-testid='stAppViewContainer'] .main { width:100% !important; max-width:100% !important; min-width:0 !important; overflow-x:hidden !important; }
        [data-testid='stAppViewContainer'] .main .block-container { width:100% !important; max-width:100% !important; min-width:0 !important; margin:0 !important; padding:.3rem .42rem .8rem !important; }
        div[data-testid='stHorizontalBlock'] { width:100% !important; max-width:100% !important; min-width:0 !important; flex-direction:column !important; flex-wrap:nowrap !important; align-items:stretch !important; gap:.16rem !important; }
        div[data-testid='stHorizontalBlock'] > div[data-testid='column'] { width:100% !important; max-width:100% !important; min-width:0 !important; flex:1 1 100% !important; padding-left:0 !important; padding-right:0 !important; overflow:hidden !important; }
        [data-testid='stVerticalBlock'], [data-testid='stVerticalBlockBorderWrapper'], [data-testid='stElementContainer'] { min-width:0 !important; max-width:100% !important; }
        /* Final compact mobile pass: uploaders and notices must not dominate the phone screen. */
        @media (max-width:900px) {
            [data-testid='stAppViewContainer'] .main .block-container { width:100% !important; max-width:100% !important; min-width:0 !important; margin:0 !important; padding:.22rem .42rem .7rem !important; }
            div[data-testid='stHorizontalBlock'] { width:100% !important; min-width:0 !important; max-width:100% !important; }
            div[data-testid='stHorizontalBlock'] > div[data-testid='column'] { min-width:0 !important; max-width:100% !important; overflow:hidden !important; }
            [data-testid='stVerticalBlock'], [data-testid='stVerticalBlockBorderWrapper'], [data-testid='stElementContainer'] { min-width:0 !important; max-width:100% !important; }
            .stSelectbox div[data-baseweb='select'] > div, .stTextInput input, .stNumberInput input { min-width:0 !important; width:100% !important; }
            [data-testid='stFileUploader'], [data-testid='stFileUploaderDropzone'] { max-width:100% !important; min-width:0 !important; }
            div[data-testid='stHorizontalBlock']:has(.mgk-three-cell) { flex-direction:row !important; flex-wrap:nowrap !important; align-items:stretch !important; gap:.14rem !important; }
            div[data-testid='stHorizontalBlock']:has(.mgk-three-cell) > div[data-testid='column'] { width:0 !important; min-width:0 !important; flex:1 1 0 !important; overflow:visible !important; min-height:2.68rem !important; padding:.16rem !important; border:1px solid rgba(132,158,234,.24) !important; border-radius:8px !important; background:linear-gradient(145deg,rgba(32,43,73,.78),rgba(13,19,36,.9)) !important; box-shadow:inset 0 1px 0 rgba(255,255,255,.045),0 4px 10px rgba(0,0,0,.12) !important; }
            div[data-testid='stHorizontalBlock']:has(.mgk-three-cell) > div[data-testid='column']:focus-within { border-color:rgba(94,214,255,.68) !important; box-shadow:0 0 0 1px rgba(94,214,255,.18),0 5px 14px rgba(0,0,0,.2) !important; }
            div[data-testid='stHorizontalBlock']:has(.mgk-three-cell) [data-testid='stWidgetLabel'] { color:#aec7ee !important; font-size:.52rem !important; font-weight:800 !important; line-height:1.05 !important; margin-bottom:.06rem !important; white-space:normal !important; letter-spacing:.01em !important; }
            div[data-testid='stHorizontalBlock']:has(.mgk-three-cell) .stSelectbox div[data-baseweb='select'] > div { min-height:1.62rem !important; padding:.06rem .12rem !important; font-size:.56rem !important; border-radius:5px !important; background:rgba(8,14,28,.72) !important; }
            div[data-testid='stHorizontalBlock']:has(.mgk-three-cell) [data-testid='stFileUploader'] { padding:.06rem !important; min-height:1.75rem !important; border-radius:5px !important; }
            div[data-testid='stHorizontalBlock']:has(.mgk-three-cell) [data-testid='stFileUploaderDropzone'] { min-height:1.55rem !important; padding:.04rem !important; border:0 !important; }
            div[data-testid='stHorizontalBlock']:has(.mgk-three-cell) [data-testid='stFileUploaderDropzone'] button { min-height:1.45rem !important; padding:.1rem .18rem !important; font-size:.54rem !important; }
            div[data-testid='stHorizontalBlock']:has(.mgk-three-cell) [data-testid='stCheckbox'] { padding:.02rem !important; border:0 !important; border-radius:0 !important; background:transparent !important; min-height:1.82rem !important; }
            div[data-testid='stHorizontalBlock']:has(.mgk-three-cell) [data-testid='stCheckbox'] label { color:#e3edff !important; font-size:.53rem !important; font-weight:700 !important; line-height:1.06 !important; }
            div[data-testid='stHorizontalBlock']:has(.mgk-three-cell) .stTextInput input { min-height:1.68rem !important; padding:.08rem .14rem !important; font-size:.56rem !important; }
            [data-testid='stVerticalBlockBorderWrapper']:has(.mgk-card-head) { padding:.56rem !important; border-radius:10px !important; }
            .mgk-card-head { font-size:.9rem; margin-bottom:.32rem; }
            .mgk-card-head b { font-size:1rem; }
            .mgk-app-brand { min-height:1.8rem; font-size:.9rem; }
            .mgk-app-brand span { width:1.38rem; height:1.38rem; border-radius:.36rem; }
            [data-testid='stPopover'] > button { min-height:1.8rem !important; padding:.18rem .35rem !important; font-size:.63rem !important; }
            [data-testid='stAppViewContainer'] .main .block-container { padding:.12rem .28rem .5rem !important; }
            [data-testid='stFileUploader'] { min-height:0 !important; padding:.1rem !important; border-radius:6px !important; box-shadow:none !important; background:rgba(18,25,46,.7) !important; }
            [data-testid='stFileUploaderDropzone'] { min-height:0 !important; padding:.1rem !important; border-radius:5px !important; background:transparent !important; border-style:dashed !important; }
            [data-testid='stFileUploaderDropzoneInstructions'] { display:none !important; }
            [data-testid='stFileUploaderDropzone'] button { min-height:1.55rem !important; padding:.16rem .42rem !important; font-size:.6rem !important; border-radius:6px !important; }
            [data-testid='stFileUploader'] small { display:none !important; }
            [data-testid='stAlert'] { padding:.1rem !important; border-radius:6px !important; font-size:.61rem !important; line-height:1.15 !important; }
            [data-testid='stVerticalBlockBorderWrapper'] { padding:.1rem !important; border-radius:6px !important; }
            [data-testid='stVerticalBlock'] > [data-testid='stVerticalBlock'] { gap:.1rem !important; }
            .stMarkdown { margin-bottom:.02rem !important; }
            .stMarkdown p { margin:.1rem 0 !important; }
            [data-testid='stHorizontalBlock'] { gap:.1rem !important; margin-bottom:.1rem !important; }
            h1, h2, h3 { margin-top:.15rem !important; margin-bottom:.1rem !important; }
            hr { margin:.18rem 0 !important; }
        }
        /* Desktop layout restoration: keep the original wider workspace and horizontal control rows. */
        [data-testid='stAppViewContainer'] .main .block-container { width:100% !important; max-width:none !important; margin:0 auto !important; padding:1rem 1.5rem 2.5rem !important; }
        div[data-testid='stHorizontalBlock'] { width:100% !important; max-width:none !important; flex-direction:row !important; flex-wrap:nowrap !important; align-items:stretch !important; gap:.75rem !important; }
        div[data-testid='stHorizontalBlock'] > div[data-testid='column'] { width:0 !important; max-width:none !important; flex:1 1 0 !important; padding-left:0 !important; padding-right:0 !important; overflow:visible !important; }
        div[data-testid='stHorizontalBlock']:has(.mgk-three-cell) > div[data-testid='column'] { min-height:0 !important; padding:.35rem !important; border:1px solid rgba(132,158,234,.2) !important; border-radius:10px !important; background:rgba(18,25,46,.5) !important; box-shadow:none !important; }
        div[data-testid='stHorizontalBlock']:has(.mgk-three-cell) [data-testid='stWidgetLabel'] { color:#c9d8f3 !important; font-size:.72rem !important; font-weight:700 !important; }
        div[data-testid='stHorizontalBlock']:has(.mgk-three-cell) .stSelectbox div[data-baseweb='select'] > div { min-height:2.35rem !important; padding:.22rem .45rem !important; font-size:.82rem !important; }
        div[data-testid='stHorizontalBlock']:has(.mgk-three-cell) [data-testid='stCheckbox'] { min-height:2.3rem !important; padding:.18rem !important; }
        div[data-testid='stHorizontalBlock']:has(.mgk-three-cell) [data-testid='stCheckbox'] label { color:#e8efff !important; font-size:.72rem !important; }
        div[data-testid='stHorizontalBlock']:has(.mgk-three-cell) .stTextInput input { min-height:2.35rem !important; padding:.22rem .45rem !important; font-size:.82rem !important; }
        [data-testid='stVerticalBlockBorderWrapper']:has(.mgk-card-head) { padding:1rem !important; border-radius:16px !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def run_one_click_youtube_export(video_path: Path, duration_seconds: int, tone: str, mode: str, voice: str, voice_style: str, audio_speed: float, blur_enabled: bool, blur_masks: list[dict], blur_strength: int, blur_background: str, blur_color: str, export_style: dict, logo_path: str | None, logo_position: str, logo_motion: str, moving_logo_text: str, text_position: str, effect_auto_zoom: bool, effect_color_filter: bool, effect_pitch_alter: bool, subtitle_enabled: bool = True, progress_callback=None, timing_basis: str = "Audio အချိန်", effect_background_blur: bool = False, effect_freeze_bypass: bool = False, cached_script: str | None = None, cached_audio: bytes | None = None, target_platform: str | None = None, quality_mode: str | None = None, original_audio_mode: str = "မူရင်းအသံအကုန်ဖျောက်", background_music_path: str | None = None, background_music_volume: int = 12) -> tuple[bytes, str, str, bytes]:
    """Run the complete YouTube workflow once, using the current UI selections."""
    script = str(cached_script or "").strip()
    if script:
        if progress_callback:
            progress_callback(20, "ရှိပြီးသား Script သုံးနေသည်")
    else:
        if progress_callback:
            progress_callback(8, "Script ရေးနေသည်")
        script = generate_recap_script(video_path, "Burmese (မြန်မာ)", int(duration_seconds), tone, mode)
    edited_video = video_path
    mirror_enabled = bool(st.session_state.get("effect_mirror", False))
    if mirror_enabled:
        edited_video = apply_copyright_edit(video_path, True, False, False, False)
    if blur_enabled and blur_masks:
        export_masks = blurred_boxes_for_mirror(video_path, blur_masks) if mirror_enabled else blur_masks
        boxes = [(int(mask["x"]), int(mask["y"]), int(mask["width"]), int(mask["height"])) for mask in export_masks]
        edited_video = apply_region_blur(edited_video, boxes, blur_strength, blur_background, blur_color)
    audio = bytes(cached_audio or b"")
    if audio:
        if progress_callback:
            progress_callback(48, "ရှိပြီးသား Voiceover သုံးနေသည်")
    else:
        if progress_callback:
            progress_callback(35, "Voiceover ပြင်နေသည်")
        audio = generate_segmented_voiceover(script, voice, voice_style, progress_callback)
    raw_duration = max(0.1, len(audio) / (24000 * 2))
    source_duration = get_video_duration(edited_video) or raw_duration
    sync_plan = calculate_sync_plan(source_duration, raw_duration, audio_speed, timing_basis)
    adjusted_duration = sync_plan["target"]
    if progress_callback:
        progress_callback(62, "စာတန်းထိုး ပြင်နေသည်" if subtitle_enabled else "Export settings ပြင်နေသည်")
    # Always create an SRT download from the narration. The toggle controls
    # whether it is burned into the MP4, not whether the user can download it.
    try:
        rendered_pcm = adjust_pcm_audio_speed(audio, sync_plan["render_audio_speed"])
        measured_render_duration = max(0.1, len(rendered_pcm) / (24000 * 2))
    except Exception:
        measured_render_duration = adjusted_duration
    srt_duration = measured_render_duration if timing_basis == "Audio အချိန်" else adjusted_duration
    generated_srt = normalize_srt_text(script_to_srt(script, srt_duration))
    if progress_callback:
        progress_callback(78, "Video ဖိုင် ပေါင်းနေသည်")
    selected_platform = target_platform if target_platform in PLATFORM_OPTIONS else st.session_state.get("target_platform", "YouTube")
    if selected_platform not in PLATFORM_OPTIONS:
        selected_platform = "YouTube"
    selected_quality = quality_mode if quality_mode in {"720", "1280"} else st.session_state.get("quality_mode", "720")
    if selected_quality not in {"720", "1280"}:
        selected_quality = "720"
    merged = merge_audio_video(
        edited_video,
        audio,
        selected_platform,
        audio_speed,
        subtitle_srt=generated_srt if subtitle_enabled else "",
        subtitle_is_final_timeline=True,
        subtitle_font=export_style.get("font") or "Noto Sans Myanmar",
        subtitle_size=export_style.get("size", 34),
        subtitle_text_color=export_style.get("text_color", "#FFFFFF"),
        subtitle_outline_color=export_style.get("outline_color", "#000000"),
        subtitle_background_mode=export_style.get("background_mode", "Transparent"),
        subtitle_background_color=export_style.get("background_color", "#000000"),
        subtitle_background_opacity=export_style.get("background_opacity", 55),
        subtitle_position=export_style.get("position", "Bottom"),
        subtitle_x=export_style.get("x", 50),
        subtitle_y=export_style.get("y", 86),
        effect_mirror=False,
        effect_auto_zoom=effect_auto_zoom,
        effect_color_filter=effect_color_filter,
        effect_pitch_alter=effect_pitch_alter,
        effect_background_blur=effect_background_blur,
        effect_freeze_bypass=effect_freeze_bypass,
        logo_path=logo_path,
        logo_position=logo_position,
        logo_motion=logo_motion,
        moving_logo_text=moving_logo_text,
        text_position=text_position,
        quality_mode=selected_quality,
        timing_basis=timing_basis,
    )
    if original_audio_mode == "တိုက်ခိုက်သံထား" or background_music_path:
        if progress_callback:
            progress_callback(90, "မူရင်းအသံ / Background Music ပေါင်းနေသည်")
        merged = mix_final_audio_layers(
            merged,
            edited_video,
            original_audio_mode,
            background_music_path,
            background_music_volume,
            sync_plan["video_speed"],
        )
    if progress_callback:
        progress_callback(100, "Video ဖိုင် ပေါင်းနေသည်")
    return merged, script, generated_srt, audio


def legacy_main():
    apply_cinematic_theme()
    brand_col, settings_col = st.columns([6, 1], gap="small")
    with brand_col:
        st.markdown("<div class='mgk-app-brand'><span>▣</span> Mg Khant</div>", unsafe_allow_html=True)
    with settings_col:
        render_menu()
    with st.container(border=True):
        st.markdown("<div class='mgk-card-head'><b>1.</b> Video ပြင်ဆင်ရန်</div>", unsafe_allow_html=True)
        upload = st.file_uploader("Video ဖိုင်တင်မယ်", type=["mp4", "mov", "mkv", "avi", "webm"])

        if upload:
            upload_token = f"upload:{upload.name}:{upload.size}"
            if st.session_state.get("last_upload_token") != upload_token:
                activate_video_source(save_upload(upload), upload.name, upload_token)
                st.session_state.last_upload_token = upload_token

        if not st.session_state.get("video_path"):
            st.warning("Video ဖိုင်တစ်ခု တင်ပါ။")
            st.stop()

        if st.session_state.get("subtitle_pipeline_version") != SUBTITLE_PIPELINE_VERSION:
            st.session_state.output_video = None
            st.session_state.subtitle_pipeline_version = SUBTITLE_PIPELINE_VERSION
            # Existing sessions may retain an older legacy-font choice. Default
            # them once to the Unicode-safe Noto build; users can still choose
            # any supported font afterwards.
            st.session_state["subtitle_font"] = "Noto Sans Myanmar"

        video_duration = get_video_duration(st.session_state.video_path)
        # A style change invalidates the previous rendered MP4; do not show stale white subtitles.
        output_signature = st.session_state.get("output_style_signature")
        current_signature = subtitle_style_signature()
        if st.session_state.get("output_video") and output_signature and tuple(output_signature) != current_signature:
            st.session_state.output_video = None
        # Final output belongs only in the bottom export-result area.  The upper
        # preview remains the original/current edit source so it does not jump
        # after a successful final render.
        persistent_preview = (st.session_state.get("blurred_video_path") or st.session_state.get("copyright_video_path") or st.session_state.video_path)
        st.markdown("<div class='workflow-fixed-preview'>", unsafe_allow_html=True)
        st.video(persistent_preview)
        st.caption("လက်ရှိ Edit Preview")
        st.markdown("</div>", unsafe_allow_html=True)
        # Single vertical workflow: all panels remain in one scrollable page.
        active_step = 1
        st.markdown("<div class='video-meta-strip'>", unsafe_allow_html=True)
        active_path = Path(st.session_state.video_path)
        active_size = active_path.stat().st_size if active_path.exists() else 0
        st.caption(f"{st.session_state.get('video_name', 'Video')} · ⏱ {format_duration(video_duration) if video_duration else '--:--'} · {active_size / 1024 / 1024:.1f} MB")
        st.markdown("</div>", unsafe_allow_html=True)
        language = "Burmese (မြန်မာ)"
        video_controls_one = st.columns(3, gap="small")
        with video_controls_one[0]:
            st.markdown("<div class='mgk-three-cell'></div>", unsafe_allow_html=True)
            platform = st.selectbox("Platform", list(PLATFORM_OPTIONS.keys()), key="target_platform")
        with video_controls_one[1]:
            st.markdown("<div class='mgk-three-cell'></div>", unsafe_allow_html=True)
            quality_mode = st.selectbox("Quality", ["720", "1280"], key="quality_mode", help="720 သို့မဟုတ် 1280 ကိုရွေးပါမယ်။")
        with video_controls_one[2]:
            st.markdown("<div class='mgk-three-cell'></div>", unsafe_allow_html=True)
            source_kind = st.selectbox("Type", ["Movie Recap", "Simple Movie"], key="source_kind", help="Movie Recap = နိုင်ငံခြား Recap လုပ်ပြီးသား Video · Simple Movie = ရိုးရိုး Movie/Scene Video")
        tone = st.selectbox("Script style", ["Cinematic and concise", "Fast TikTok style", "Calm documentary", "Dramatic storyteller"])
        platform_preset = PLATFORM_OPTIONS[platform]
        st.caption(f"{platform} · {platform_preset['ratio']} · {platform_preset['width']}×{platform_preset['height']}")
        mode = "Faithful full translation" if source_kind == "Movie Recap" else "Original recap"
        duration_valid = bool(video_duration)
        duration_seconds = video_duration or 0
        if video_duration:
            st.caption(f"Video အရှည်: {format_duration(video_duration)}")
        else:
            st.warning("Video အရှည်ကို မဖတ်နိုင်ပါ။ MP4 အဖြစ် ပြောင်းပြီး ထပ်တင်ပါ။")

    saved_speed_label = st.session_state.get("video_speed", "1×")
    try:
        audio_speed = float(str(saved_speed_label).replace("×", ""))
    except (TypeError, ValueError):
        audio_speed = 1.0
    voiceover_duration = (len(st.session_state.audio) / (24000 * 2)) if st.session_state.get("audio") else None

    if False:
        st.divider()
        st.subheader("ရွေးပြီး Video ထုတ်ရန်")
        st.caption("Video → Effect → Blur → အသံ + SRT → Logo → Export")
        if False and st.button("Video ထုတ်မယ် →", type="primary", use_container_width=True, key="one-click-youtube-export"):
            if not st.session_state.get("video_path") or not duration_valid:
                st.warning("Video တင်ပြီး Recap အရှည်ကို မှန်ကန်အောင် ရွေးပါ။")
                st.stop()
            with st.status("Video အားလုံးကို အစဉ်လိုက်ထုတ်နေပါတယ်...", expanded=True) as pipeline_status:
                try:
                    export_style = st.session_state.get("subtitle_export_style", {})
                    pipeline_status.write("Script ရေးနေပါတယ်...")
                    existing_script = str(st.session_state.get("script", "")).strip()
                    existing_audio = st.session_state.get("audio") or b""
                    merged, generated_script, generated_srt, generated_audio = run_one_click_youtube_export(
                        Path(st.session_state.video_path), int(duration_seconds), tone, mode,
                        st.session_state.get("voice", "Aoede"), st.session_state.get("voice_style", "cinematic narrator"),
                        float(str(st.session_state.get("video_speed", "1×")).replace("×", "")),
                        bool(st.session_state.get("blur_enabled", False)), st.session_state.get("blur_masks", []),
                        int(st.session_state.get("blur_strength", 18)), st.session_state.get("blur_background_style", "None"),
                        st.session_state.get("solid_box_color", "#16B8FF"), export_style,
                        st.session_state.get("logo_overlay_path"), st.session_state.get("logo_position", "Right"),
                        st.session_state.get("logo_motion", "Left static"), st.session_state.get("moving_logo_text", ""),
                        st.session_state.get("text_position", "Bottom center"),
                        bool(st.session_state.get("effect_auto_zoom", False)),
                        bool(st.session_state.get("effect_color_filter", False)),
                        bool(st.session_state.get("effect_pitch_alter", False)),
                        cached_script=existing_script or None,
                        cached_audio=existing_audio if existing_script else None,
                    )
                    st.session_state.script = generated_script
                    st.session_state.audio = generated_audio
                    st.session_state.generated_srt = generated_srt
                    st.session_state.subtitle_srt_editor = generated_srt
                    st.session_state.output_video = merged
                    st.session_state.one_click_complete = True
                    pipeline_status.update(label="YouTube Video ပြီးပါပြီ", state="complete", expanded=False)
                    st.success("YouTube Video + အသံ + SRT + ရွေးထားသော Logo/Effect အားလုံး တစ်ခါတည်း ပြီးပါပြီ။")
                    st.rerun()
                except Exception as exc:
                    pipeline_status.update(label="Export မအောင်မြင်ပါ", state="error", expanded=True)
                    st.error(f"Video export မအောင်မြင်ပါ: {api_error_message(exc)}")
        st.subheader("1 · Script")
        st.caption("လိုအပ်တဲ့ Script ကို ဒီမှာ ပြင်နိုင်ပါတယ်။")
        st.session_state.script = st.text_area("Editable narration", st.session_state.script, height=130)
        st.download_button("Script ဒေါင်းရန်", st.session_state.script, file_name="recap-script.txt", mime="text/plain")

    with st.container(border=True):
        st.markdown("<div class='mgk-card-head'><b>2.</b> Effect ရွေးရန်</div>", unsafe_allow_html=True)
        st.caption("လိုအပ်တဲ့ Effect ကိုသာ ရွေးပါ")
        effect_steps = [
            ("effect_mirror", "Video Flip / Mirror", "Apply Edit မှာ Video ကို ဘယ်/ညာ လှန်ပေးမယ်"),
            ("effect_auto_zoom", "Auto Zoom", "နောက်ဆုံး Video export မှာ Zoom ထည့်ပေးမယ်"),
            ("effect_color_filter", "Color Filter", "နောက်ဆုံး Video export မှာ အရောင်ပြောင်းပေးမယ်"),
            ("effect_pitch_alter", "Audio Pitch Alter", "နောက်ဆုံး Audio export မှာ အသံ Pitch ပြောင်းပေးမယ်"),
            ("effect_background_blur", "Background Blur", "အရွယ်မကိုက်တဲ့ Video ရဲ့ လွတ်နေရာကို Blur နောက်ခံဖြည့်ပေးမယ်"),
            ("effect_freeze_bypass", "Freeze + Zoom Bypass", "Final Video အစမှာ ခဏ Freeze လုပ်ပြီး Zoom အနည်းငယ်ထည့်ပေးမယ်"),
        ]
        effect_columns = st.columns(3, gap="small")
        for position, (effect_key, effect_label, effect_hint) in enumerate(effect_steps):
            with effect_columns[position % 3]:
                st.markdown("<div class='mgk-three-cell'></div>", unsafe_allow_html=True)
                selected = st.checkbox(effect_label, value=bool(st.session_state.get(effect_key, False)), key=f"effect-card-{effect_key}", help=effect_hint)
                st.session_state[effect_key] = selected
        st.markdown("<div class='mgk-card-subhead'>Audio Mix</div>", unsafe_allow_html=True)
        audio_controls = st.columns(3, gap="small")
        with audio_controls[0]:
            st.markdown("<div class='mgk-three-cell'></div>", unsafe_allow_html=True)
            original_audio_mode = st.selectbox(
                "မူရင်းအသံ",
                ["မူရင်းအသံအကုန်ဖျောက်", "တိုက်ခိုက်သံထား"],
                key="original_audio_mode",
                help="တိုက်ခိုက်သံထားက မူရင်းအသံကိုသေးသေးထားပြီး Action/SFX ကိုတတ်နိုင်သလောက်ဆက်ထားပေးမယ်။",
            )
        with audio_controls[1]:
            st.markdown("<div class='mgk-three-cell'></div>", unsafe_allow_html=True)
            music_upload = st.file_uploader("Music", type=["mp3", "wav", "m4a", "aac", "ogg"], key="background_music_upload", label_visibility="collapsed")
        with audio_controls[2]:
            st.markdown("<div class='mgk-three-cell'></div>", unsafe_allow_html=True)
            requested_music_volume = st.slider("Music", 0, 35, int(st.session_state.get("background_music_volume", 12)), key="background_music_volume")
        if music_upload and st.session_state.get("background_music_name") != music_upload.name:
            st.session_state.background_music_path = str(save_upload(music_upload))
            st.session_state.background_music_name = music_upload.name
        if st.session_state.get("background_music_path"):
            st.caption(f"Music: {st.session_state.get('background_music_name', 'Music file')}")
            background_music_volume = requested_music_volume
        else:
            background_music_volume = 0
            st.caption("Music မထည့်လည်းရပါတယ်")

    with st.container(border=True):
        st.markdown("<div class='mgk-card-head'><b>3.</b> Logo</div>", unsafe_allow_html=True)
        st.caption("ပုံ")
        logo_upload = st.file_uploader("Logo ပုံထည့်ရန်", type=["png", "jpg", "jpeg", "webp"], label_visibility="collapsed", key="logo_upload")
        if logo_upload and st.session_state.get("logo_upload_name") != logo_upload.name:
            st.session_state.logo_overlay_path = str(persist_logo_upload(logo_upload))
            st.session_state.logo_upload_name = logo_upload.name
        logo_controls = st.columns(3, gap="small")
        with logo_controls[0]:
            st.markdown("<div class='mgk-three-cell'></div>", unsafe_allow_html=True)
            logo_position = st.selectbox("ပုံနေရာ", ["Left", "Right"], format_func=lambda value: "ဘယ်" if value == "Left" else "ညာ", key="logo_position")
        with logo_controls[1]:
            st.markdown("<div class='mgk-three-cell'></div>", unsafe_allow_html=True)
            moving_logo_text = st.text_input("Logo စာ", key="moving_logo_text", placeholder="စာပဲထည့်လည်းရပါတယ်", label_visibility="collapsed")
        with logo_controls[2]:
            st.markdown("<div class='mgk-three-cell'></div>", unsafe_allow_html=True)
            st.session_state.setdefault("logo_motion", "Left static")
            logo_motion = st.selectbox("စာသား", ["Left static", "Right static", "Full-screen movement"], format_func=lambda value: {"Left static": "ဘယ်ငြိမ်", "Right static": "ညာငြိမ်", "Full-screen movement": "အနှံ့ပြေး"}[value], key="logo_motion")
        text_position = "Bottom center"
        if st.session_state.get("logo_overlay_path"):
            st.caption(f"Logo အသင့်ဖြစ်ပါပြီ · {st.session_state.get('logo_upload_name', 'Logo')} · {logo_position} · {logo_motion}")
        if False and st.button("Apply Edit →", key="apply-copyright-edit", type="primary", use_container_width=True):
            with st.spinner("Copyright Edit ကို Video ပေါ်မှာ ထည့်နေပါတယ်..."):
                try:
                    # Always apply Mirror from the original upload so repeated
                    # clicks never flip an already-flipped file a second time.
                    edit_source = Path(st.session_state.video_path)
                    st.session_state.copyright_video_path = str(apply_copyright_edit(
                        edit_source,
                        bool(st.session_state.get("effect_mirror", False)),
                        False,  # Auto Zoom is applied at Video export.
                        False,  # Color Filter is applied at Video export.
                        False,  # Pitch Alter is applied with the final audio.
                    ))
                    st.session_state.blurred_video_path = None
                    st.session_state.output_video = None
                    st.session_state.workflow_step = 3
                    st.success("Copyright Edit ပြီးပါပြီ။ အပေါ်က Preview လည်း ပြောင်းပြီး Blue Mask အဆင့်ကို ဖွင့်ထားပါတယ်။")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Copyright Edit မအောင်မြင်ပါ: {exc}")

    if True:
        st.divider()
        st.subheader("4 · Blur ရွေးရန်")
        st.caption("Copy Edit ပြီးမှ နောက်အဆင့်မှာ Blur Mask ဆက်လုပ်နိုင်ပါတယ်။")
        st.caption("မူရင်း Video ကို အရင်ကြည့်ပြီး Blur လုပ်ချင်တဲ့ စာတန်း/နေရာကို ရွေးပါ။ Frame ပုံမထွက်ရင်လည်း အောက်က Video ကိုကြည့်ပြီး Box ကို ဆက်ချိန်နိုင်ပါတယ်။")
        st.caption("ဒီ Video ကို အပေါ်က Persistent Preview မှာပဲ ကြည့်ပြီး Blur Box ကို ချိန်ပါ။")
        try:
            blur_source = Path(st.session_state.get("copyright_video_path") or st.session_state.video_path)
            duration_for_frames = get_video_duration(blur_source)
            frame_times = list(sampled_frame_times(duration_for_frames) or [0.0])
            if not frame_times:
                frame_times = [0.0]
            frame_labels = [f"{format_duration(round(value))} မှာ Frame" for value in frame_times]
            selected_label = st.selectbox("စာတန်းအများဆုံး/အရှည်ဆုံးပေါ်တဲ့ Frame ရွေးပါ", frame_labels, key="blur_frame_choice")
            selected_frame_time = frame_times[frame_labels.index(selected_label)]
            preview_frame = None
            try:
                preview_frame = extract_preview_frame(blur_source, selected_frame_time)
            except Exception as frame_exc:
                st.warning(f"Frame ပုံကို မထုတ်နိုင်သေးပါ။ မူရင်း Video Preview ကိုကြည့်ပြီး Box ကို ဆက်ချိန်နိုင်ပါတယ်: {frame_exc}")
            dimensions = get_video_dimensions(blur_source)
            if dimensions:
                original_width, original_height = dimensions
            elif preview_frame:
                original_width, original_height = preview_frame.width, preview_frame.height
            else:
                original_width, original_height = 1280, 720
            original_width = max(2, int(original_width))
            original_height = max(2, int(original_height))
            if preview_frame:
                preview_width = min(720, preview_frame.width)
                preview_height = max(240, round(preview_frame.height * preview_width / preview_frame.width))
                scale_x = preview_width / original_width
                scale_y = preview_height / original_height
            else:
                preview_width = min(720, original_width)
                preview_height = max(240, round(original_height * preview_width / original_width))
                scale_x = preview_width / original_width
                scale_y = preview_height / original_height
            if not isinstance(st.session_state.get("blur_masks"), list) or not st.session_state.blur_masks:
                st.session_state.blur_masks = [{"x": original_width // 10, "y": original_height * 3 // 4, "width": original_width // 2, "height": max(10, original_height // 8)}]
            blur_enabled = st.toggle("BLUR MASK (MAX 3)", value=st.session_state.get("blur_enabled", False))
            st.session_state.blur_enabled = blur_enabled
            if blur_enabled:
                st.caption("Copy Edit မှာ ရွေးထားတဲ့ Anti-Copyright Effect တွေကို Blur Export နဲ့အတူ အသုံးချပါမယ်။")
            else:
                st.caption("Blur Mask ပိတ်ထားပါက Blur မလုပ်ဘဲ ဆက်သွားပါမယ်။")
            if not blur_enabled:
                st.session_state.blurred_video_path = None
                st.session_state.pop("subtitle_text", None)
            if blur_enabled:
                control_col, preview_col = st.columns([1, 1.35], gap="medium")
                with control_col:
                    if st.button("+ Add Blur Box", disabled=len(st.session_state.blur_masks) >= 3):
                        st.session_state.blur_masks.append({"x": original_width // 10, "y": original_height // 3, "width": original_width // 3, "height": max(10, original_height // 8)})
                        st.rerun()
                    background_style = st.selectbox("Background Style", ["None", "Transparent", "Solid Box"], help="Solid Box ကိုရွေးရင် ရွေးထားတဲ့အရောင်နဲ့ ဖုံးပေးမယ်။")
                    solid_box_color = st.color_picker("Solid Box Color", "#16B8FF", key="solid_box_color") if background_style == "Solid Box" else "#16B8FF"
                    blur_strength = st.slider("Blur Strength", 2, 40, 18)
                    preview_boxes = []
                    for index, mask in enumerate(st.session_state.blur_masks):
                        st.markdown(f"**Blur Box {index + 1}**")
                        box_left, box_right = st.columns(2)
                        with box_left:
                            mask["x"] = st.slider(f"X Position · Box {index + 1}", 0, max(0, original_width - 4), min(mask["x"], max(0, original_width - 4)), step=2, key=f"mask-x-{index}")
                            mask["y"] = st.slider(f"Y Position · Box {index + 1}", 0, max(0, original_height - 4), min(mask["y"], max(0, original_height - 4)), step=2, key=f"mask-y-{index}")
                        with box_right:
                            mask["width"] = st.slider(f"Width · Box {index + 1}", 4, max(4, original_width - mask["x"]), min(mask["width"], max(4, original_width - mask["x"])), step=2, key=f"mask-w-{index}")
                            mask["height"] = st.slider(f"Height · Box {index + 1}", 4, max(4, original_height - mask["y"]), min(mask["height"], max(4, original_height - mask["y"])), step=2, key=f"mask-h-{index}")
                        preview_boxes.append((round(mask["x"] * scale_x), round(mask["y"] * scale_y), round(mask["width"] * scale_x), round(mask["height"] * scale_y)))
                with preview_col:
                    if preview_frame:
                        st.image(draw_blur_selection(preview_frame.resize((preview_width, preview_height)), preview_boxes, background_style), use_container_width=True)
                    else:
                        st.info("Frame preview မရသေးပါ။ အပေါ်က မူရင်း Video ကိုကြည့်ပြီး Box နေရာကို ချိန်နိုင်ပါတယ်။ Apply လုပ်တဲ့အခါ Video တစ်ခုလုံးမှာ Blur ထည့်ပေးပါမယ်။")
                    if False and st.button("Apply Blur →", type="primary", use_container_width=True):
                        with st.spinner("Blur Mask ကို Video တစ်ခုလုံးပေါ်မှာ ထည့်နေပါတယ်..."):
                            st.session_state.blurred_video_path = None
                            try:
                                boxes = [(int(mask["x"]), int(mask["y"]), int(mask["width"]), int(mask["height"])) for mask in st.session_state.blur_masks]
                                st.session_state.blurred_video_path = str(apply_region_blur(blur_source, boxes, blur_strength, background_style, solid_box_color))
                                st.session_state.output_video = None
                                st.session_state.workflow_step = 4
                                st.session_state.audio = None
                                st.success("Blur Mask အောင်မြင်ပါပြီ။ အပေါ်က Preview လည်း ပြောင်းပြီး Voiceover အဆင့်ကို ဖွင့်ထားပါတယ်။")
                                st.rerun()
                            except Exception as exc:
                                st.session_state.blurred_video_path = None
                                st.session_state.pop("subtitle_text", None)
                                st.error(f"Blur မအောင်မြင်ပါ။ Subtitle အဆင့်ကို Skip လုပ်ပြီး ဆက်သွားပါမယ်: {exc}")
            else:
                st.info("Blur မလုပ်ချင်ရင် အောက်က ခလုတ်ကိုနှိပ်ပြီး Voiceover ကို တိုက်ရိုက်ဆက်နိုင်ပါတယ်။")
                if False and st.button("Blur မလုပ်ဘဲ Voiceover သို့ ဆက်မယ် →", key="skip-blur-step", use_container_width=True):
                    st.session_state.blur_enabled = False
                    st.session_state.blurred_video_path = None
                    st.session_state.workflow_step = 4
                    st.rerun()
        except Exception as exc:
            st.session_state.blurred_video_path = None
            st.session_state.pop("subtitle_text", None)
            st.error(f"Blue Mask control မဖွင့်နိုင်ပါ။ မူရင်း Video ကို ပြန်တင်ပြီး ထပ်စမ်းပါ: {exc}")

        if not st.session_state.get("blurred_video_path"):
            st.info("Blue Mask မအောင်မြင်သေးတဲ့အတွက် Blur နဲ့ မြန်မာစာတန်းထိုးအဆင့်ကို Skip လုပ်ထားပါတယ်။ Voiceover အဆင့်ကို ဆက်လုပ်နိုင်ပါတယ်။")

    if True:
        st.divider()
        st.subheader("5 · အသံ ရွေးရန်")
        voice_row = st.columns(3, gap="small")
        with voice_row[0]:
            speed_label = st.selectbox("Audio Speed", ["0.5×", "0.75×", "1×", "1.25×", "1.5×", "2×"], index=2, key="video_speed")
        with voice_row[1]:
            voice = st.selectbox("Voice", list(VOICE_OPTIONS.keys()), format_func=lambda item: f"{item} · {VOICE_OPTIONS[item]}", key="voice")
        with voice_row[2]:
            style = st.selectbox("Voice style", ["cinematic narrator", "warm narrator", "energetic creator", "serious documentary"], key="voice_style")
        audio_speed = float(speed_label.replace("×", ""))
        timing_basis = st.selectbox("အချိန်ယူမယ်", ["Audio အချိန်", "Video အချိန်"], key="timing_basis")
        if voiceover_duration and video_duration:
            sync_plan = calculate_sync_plan(video_duration, voiceover_duration, audio_speed, timing_basis)
            adjusted_audio_duration = sync_plan["adjusted_audio"]
            auto_video_speed = sync_plan["video_speed"]
            final_duration = sync_plan["target"]
            st.info(f"{timing_basis} · အသံ {format_duration(round(voiceover_duration))} → {format_duration(round(adjusted_audio_duration))} · Video Auto-fit {auto_video_speed:.2f}× · Final {format_duration(round(final_duration))}")
        else:
            st.caption("Voiceover ထွက်ပြီးနောက် Audio မူရင်းအရှည်၊ ချိန်ပြီးအရှည်နဲ့ Video Auto-fit Speed ကို ပြပါမယ်။")
        if False and st.button("Voiceover ထုတ်မယ်", type="primary", use_container_width=True):
            with st.spinner(f"Voiceover ပြုလုပ်နေပါတယ်... ({voice})"):
                try:
                    st.session_state.audio = generate_voiceover(st.session_state.script, voice, style)
                    raw_duration = max(0.1, len(st.session_state.audio) / (24000 * 2))
                    adjusted_duration = raw_duration / max(0.5, min(2.0, float(audio_speed)))
                    script_for_srt = str(st.session_state.get("script", "")).strip()
                    if not script_for_srt:
                        raise ValueError("Script မရှိသေးလို့ SRT မဖန်တီးနိုင်ပါ။ Script အဆင့်ကို အရင်ပြီးအောင်လုပ်ပါ။")
                    st.session_state.generated_srt = normalize_srt_text(script_to_srt(script_for_srt, adjusted_duration))
                    if not st.session_state.generated_srt.strip():
                        raise ValueError("SRT အလွတ်ဖြစ်နေပါတယ်။ Script ကို ပြန်စစ်ပြီး Voiceover ပြန်ထုတ်ပါ။")
                    st.session_state.subtitle_srt_editor = st.session_state.generated_srt
                    st.session_state.subtitle_text = srt_to_plain_text(st.session_state.generated_srt)
                    st.session_state.pop("audio_preview", None)
                    st.session_state.pop("audio_preview_token", None)
                    st.session_state.subtitle_enabled = True
                    st.session_state.workflow_step = 5
                    st.success("Voiceover နဲ့ အချိန်ပါ SRT ကို တစ်ခါတည်း ဖန်တီးပြီးပါပြီ။")
                    st.rerun()
                except Exception as exc:
                    st.error(api_error_message(exc))

    if True:
        raw_audio = st.session_state.get("audio") or b""
        audio_token = (len(raw_audio), raw_audio[:16], raw_audio[-16:])
        cached_token = st.session_state.get("audio_preview_token")
        if cached_token != (audio_token, audio_speed):
            try:
                st.session_state.audio_preview = adjust_pcm_audio_speed(raw_audio, audio_speed)
                st.session_state.audio_preview_token = (audio_token, audio_speed)
            except Exception as exc:
                st.session_state.audio_preview = raw_audio
                st.session_state.audio_preview_token = (audio_token, audio_speed)
                st.warning(f"Voiceover Speed Preview မပြောင်းနိုင်ပါ။ မူရင်းအသံကို ပြထားပါတယ်: {exc}")
        preview_audio = st.session_state.get("audio_preview", raw_audio)
        preview_duration = len(preview_audio) / (24000 * 2)
        if False:
            st.caption(f"Voiceover Preview · မူရင်း {format_duration(round(voiceover_duration or 0))} → Speed {audio_speed:g}× → {format_duration(round(preview_duration))}")
            st.audio(pcm_to_wav(preview_audio), format="audio/wav")
            if False:
                st.download_button("Voiceover အသံ ဒေါင်းရန်", pcm_to_wav(preview_audio), file_name="recap-voiceover-adjusted.wav", mime="audio/wav")
            if False and raw_audio and st.button("Video + အသံ ပေါင်းပြီး ဒေါင်းရန် →", key="export-audio-video", type="primary", use_container_width=True):
                with st.spinner("Video နဲ့ အသံကို ပေါင်းနေပါတယ်..."):
                    try:
                        audio_video_source = Path(st.session_state.get("blurred_video_path") or st.session_state.get("copyright_video_path") or st.session_state.video_path)
                        st.session_state.audio_video_output = merge_audio_video(
                            audio_video_source, raw_audio, platform, audio_speed, subtitle_srt="",
                            effect_mirror=False,
                            effect_auto_zoom=bool(st.session_state.get("effect_auto_zoom", False)),
                            effect_color_filter=bool(st.session_state.get("effect_color_filter", False)),
                            effect_pitch_alter=bool(st.session_state.get("effect_pitch_alter", False)),
                            logo_path=st.session_state.get("logo_overlay_path"),
                            logo_position=st.session_state.get("logo_position", "Right"),
                            logo_motion=st.session_state.get("logo_motion", "Left static"),
                            moving_logo_text=st.session_state.get("moving_logo_text", ""),
                            text_position=st.session_state.get("text_position", "Bottom center"),
                            quality_mode=quality_mode,
                        )
                        st.session_state.output_video = st.session_state.audio_video_output
                        st.session_state.output_style_signature = None
                        st.success("Video + အသံ ပေါင်းပြီးပါပြီ။ အောက်က Download ခလုတ်ကိုနှိပ်ပါ။")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Video + အသံ မပေါင်းနိုင်ပါ: {exc}")
            if st.session_state.get("audio_video_output"):
                if False:
                    st.download_button("Video + အသံ ဖိုင် ဒေါင်းရန်", st.session_state.audio_video_output, file_name="movie-recap-with-audio.mp4", mime="video/mp4", key="download-audio-video")

        subtitle_enabled = st.toggle("စာတန်းထိုး ထည့်မယ်", value=st.session_state.get("subtitle_enabled", True), key="subtitle_enabled")
        if subtitle_enabled:
            generated_srt = normalize_srt_text(st.session_state.get("generated_srt", ""))
            if not generated_srt and st.session_state.get("script", "").strip():
                audio_duration = max(0.1, (len(st.session_state.get("audio") or b"") / (24000 * 2)) or float(video_duration or 60))
                generated_srt = normalize_srt_text(script_to_srt(st.session_state.script, audio_duration))
                st.session_state.generated_srt = generated_srt
            if generated_srt and not st.session_state.get("subtitle_srt_editor", "").strip():
                st.session_state["subtitle_srt_editor"] = generated_srt
            subtitle_control_col, subtitle_preview_col = st.columns([1, 1.35], gap="medium")
            with subtitle_control_col:
                if st.session_state.get("subtitle_font") not in SUBTITLE_FONT_OPTIONS:
                    st.session_state["subtitle_font"] = "Noto Sans Myanmar"
                subtitle_font = st.selectbox("Font", list(SUBTITLE_FONT_OPTIONS), key="subtitle_font")
                subtitle_metrics_left, subtitle_metrics_right = st.columns(2)
                with subtitle_metrics_left:
                    subtitle_size = st.slider("Size", 1, 72, 34, key="subtitle_size")
                with subtitle_metrics_right:
                    subtitle_background_opacity = st.slider("Opacity", 0, 100, 55, key="subtitle_background_opacity")
                subtitle_background_mode = st.selectbox("Background", ["Transparent", "Solid background"], key="subtitle_background_mode")
                subtitle_color_text, subtitle_color_outline, subtitle_color_background = st.columns(3)
                with subtitle_color_text:
                    subtitle_text_color = st.color_picker("စာအရောင်", "#FFFFFF", key="subtitle_text_color")
                with subtitle_color_outline:
                    subtitle_outline_color = st.color_picker("Outline", "#000000", key="subtitle_outline_color")
                with subtitle_color_background:
                    subtitle_background_color = st.color_picker("နောက်ခံ", "#000000", key="subtitle_background_color")
                subtitle_position_x, subtitle_position_y = st.columns(2)
                with subtitle_position_x:
                    subtitle_x = st.slider("X", 0, 100, int(st.session_state.get("subtitle_x", 50)), key="subtitle_x")
                with subtitle_position_y:
                    subtitle_y = st.slider("Y", 0, 100, int(st.session_state.get("subtitle_y", 78)), key="subtitle_y")
                st.session_state["subtitle_export_font"] = subtitle_font
                st.session_state["subtitle_export_style"] = {
                    "font": subtitle_font,
                    "size": subtitle_size,
                    "text_color": subtitle_text_color,
                    "outline_color": subtitle_outline_color,
                    "background_mode": subtitle_background_mode,
                    "background_color": subtitle_background_color,
                    "background_opacity": subtitle_background_opacity,
                    "position": "Bottom",
                    "x": subtitle_x,
                    "y": subtitle_y,
                }
            with subtitle_preview_col:
                try:
                    subtitle_test_source = st.session_state.get("video_path")
                    if subtitle_test_source:
                        subtitle_test_path = Path(subtitle_test_source)
                        subtitle_test_text = generated_srt or "မြန်မာစာတန်းထိုး စမ်းသပ်ခြင်း"
                        preview_width, preview_height = render_dimensions(platform, quality_mode)
                        try:
                            subtitle_test_image = render_ffmpeg_subtitle_test(
                                subtitle_test_path, subtitle_test_text, subtitle_font, subtitle_size,
                                subtitle_text_color, subtitle_outline_color, subtitle_background_mode,
                                subtitle_background_color, subtitle_background_opacity, subtitle_x, subtitle_y,
                                preview_width, preview_height, False, True,
                            )
                            st.image(subtitle_test_image, caption="Subtitle Test · ပုံအပြည့်၊ မြန်မာစာနှင့် X/Y က Final Video နဲ့တူတူ", use_container_width=True)
                        except Exception as render_exc:
                            st.error(f"Subtitle Test မပြနိုင်သေးပါ: {render_exc}")
                    else:
                        st.info("Video မတင်ရသေးလို့ Subtitle Preview မပြနိုင်သေးပါ။")
                except Exception as preview_exc:
                    st.caption(f"Preview မရသေးပါ: {preview_exc}")
        else:
            st.session_state.pop("subtitle_text", None)

        if False and st.button("စာတန်းထိုး + အသံ + Video ပေါင်းထုတ်မယ် →", use_container_width=True):
            if not st.session_state.get("audio"):
                st.warning("စာတန်းထိုး Video Export မလုပ်ခင် Voiceover ကို အရင်ထုတ်ပါ။")
                st.stop()
            with st.spinner("Audio Speed ချိန်ပြီး Video ကို အရှည်ကိုက်အောင် Auto-fit လုပ်နေပါတယ်..."):
                try:
                    source_video = Path(st.session_state.get("blurred_video_path") or st.session_state.get("copyright_video_path") or st.session_state.video_path)
                    subtitle_srt_to_burn = normalize_srt_text(st.session_state.get("subtitle_srt_editor", "") or st.session_state.get("generated_srt", ""))
                    source_duration = get_video_duration(source_video) or video_duration or 60
                    narration_duration = max(0.1, len(st.session_state.audio) / (24000 * 2))
                    shared_sync_plan = calculate_sync_plan(source_duration, narration_duration, audio_speed)
                    shared_final_duration = shared_sync_plan["target"]
                    export_width, export_height = render_dimensions(platform, st.session_state.get("quality_mode", "720"))
                    if not subtitle_srt_to_burn.strip():
                        st.warning("အောက်က SRT Box ထဲမှာ အချိန်ပါစာတန်းကို အရင်ဖြည့်ပါ။")
                        st.stop()
                    st.info(f"စာတန်းထိုး + Voiceover + Video ကို {format_duration(round(shared_final_duration if 'shared_final_duration' in locals() else preview_duration))} အရှည်တစ်ခုတည်းနဲ့ ပေါင်းထုတ်နေပါတယ်။")
                    export_style = st.session_state.get("subtitle_export_style", {})
                    st.session_state.output_video = merge_audio_video(
                        source_video,
                        st.session_state.audio,
                        platform,
                        audio_speed,
                        subtitle_srt=subtitle_srt_to_burn,
                        subtitle_font=export_style.get("font") or "ပြည်ထောင်စု 2.5.3 Bold",
                        subtitle_size=export_style.get("size", 34),
                        subtitle_text_color=export_style.get("text_color", "#FFFFFF"),
                        subtitle_outline_color=export_style.get("outline_color", "#000000"),
                        subtitle_background_mode=export_style.get("background_mode", "Transparent"),
                        subtitle_background_color=export_style.get("background_color", "#000000"),
                        subtitle_background_opacity=export_style.get("background_opacity", 55),
                        subtitle_position=export_style.get("position", "Bottom"),
                        subtitle_x=export_style.get("x", 50),
                        subtitle_y=export_style.get("y", 86),
                        # Mirror is already rendered by Apply Edit. The remaining
                        # selected effects are applied once in this final export.
                        effect_mirror=False,
                        effect_auto_zoom=bool(st.session_state.get("effect_auto_zoom", False)),
                        effect_color_filter=bool(st.session_state.get("effect_color_filter", False)),
                        effect_pitch_alter=bool(st.session_state.get("effect_pitch_alter", False)),
                        logo_path=st.session_state.get("logo_overlay_path"),
                        logo_position=st.session_state.get("logo_position", "Right"),
                        logo_motion=st.session_state.get("logo_motion", "Left static"),
                        moving_logo_text=st.session_state.get("moving_logo_text", ""),
                        text_position=st.session_state.get("text_position", "Bottom center"),
                        quality_mode=st.session_state.get("quality_mode", "720"),
                    )
                    st.session_state.output_style_signature = subtitle_style_signature(export_style)
                    register_generation()
                    st.success(f"ရွေးထားသော {export_style.get('font')} / {export_style.get('text_color')} style အတိုင်း Final Video ရပါပြီ။")
                    st.rerun()
                except Exception as exc:
                    st.error(f"FFmpeg မအောင်မြင်ပါ: {exc}")

    if True:
        st.divider()
        st.subheader("နောက်ဆုံး Video ထုတ်ရန်")
        st.caption("အောက်က ရွေးချယ်မှုအားလုံးပြီးမှ Export နှိပ်ပါ။")
        if st.button("Video ထုတ်မယ် →", type="primary", use_container_width=True, key="final-one-click-export"):
            if not st.session_state.get("video_path") or not duration_valid:
                st.warning("Video တင်ပြီး Recap အရှည်ကို မှန်ကန်အောင် ရွေးပါ။")
                st.stop()
            st.session_state.pop("thumbnail_data", None)
            st.session_state.pop("thumbnail_title", None)
            with st.status("Video အားလုံးကို အစဉ်လိုက်ထုတ်နေပါတယ်...", expanded=True) as pipeline_status:
                try:
                    export_style = st.session_state.get("subtitle_export_style", {})
                    progress_placeholder = st.empty()
                    subtitle_is_enabled = bool(st.session_state.get("subtitle_enabled", True))
                    existing_script = str(st.session_state.get("script", "")).strip()
                    existing_audio = st.session_state.get("audio") or b""
                    render_export_progress_card(progress_placeholder, 2, "Script ရေးနေသည်", subtitle_is_enabled)
                    def update_export_progress(percent, step):
                        render_export_progress_card(progress_placeholder, percent, step, subtitle_is_enabled)
                        pipeline_status.write(f"{percent}% · {step}")
                    merged, generated_script, generated_srt, generated_audio = run_one_click_youtube_export(
                        Path(st.session_state.video_path), int(duration_seconds), tone, mode,
                        st.session_state.get("voice", "Aoede"), st.session_state.get("voice_style", "cinematic narrator"),
                        audio_speed, bool(st.session_state.get("blur_enabled", False)), st.session_state.get("blur_masks", []),
                        int(st.session_state.get("blur_strength", 18)), st.session_state.get("blur_background_style", "None"),
                        st.session_state.get("solid_box_color", "#16B8FF"), export_style,
                        st.session_state.get("logo_overlay_path"), st.session_state.get("logo_position", "Right"),
                        st.session_state.get("logo_motion", "Left static"), st.session_state.get("moving_logo_text", ""),
                        st.session_state.get("text_position", "Bottom center"),
                        bool(st.session_state.get("effect_auto_zoom", False)),
                        bool(st.session_state.get("effect_color_filter", False)),
                        bool(st.session_state.get("effect_pitch_alter", False)),
                        subtitle_enabled=subtitle_is_enabled,
                        progress_callback=update_export_progress,
                        timing_basis=timing_basis,
                        effect_background_blur=bool(st.session_state.get("effect_background_blur", False)),
                        effect_freeze_bypass=bool(st.session_state.get("effect_freeze_bypass", False)),
                        cached_script=existing_script or None,
                        cached_audio=existing_audio if existing_script else None,
                        target_platform=platform,
                        quality_mode=quality_mode,
                        original_audio_mode=st.session_state.get("original_audio_mode", "မူရင်းအသံအကုန်ဖျောက်"),
                        background_music_path=st.session_state.get("background_music_path"),
                        background_music_volume=int(st.session_state.get("background_music_volume", 0)),
                    )
                    st.session_state.script = generated_script
                    st.session_state.generated_srt = generated_srt
                    st.session_state.subtitle_srt_editor = generated_srt
                    st.session_state.output_video = merged
                    st.session_state.audio = generated_audio
                    st.session_state.thumbnail_error = ""
                    render_export_progress_card(progress_placeholder, 100, "Video ဖိုင် ပေါင်းနေသည်", subtitle_is_enabled)
                    pipeline_status.update(label="Video ပြီးပါပြီ", state="complete", expanded=False)
                    export_result_label = " + မြန်မာစာတန်းထိုး" if subtitle_is_enabled else " + SRT ဖိုင်"
                    st.success("Video + အသံ + Logo + Effect အားလုံး ပြီးပါပြီ" + export_result_label + " အသင့်ဖြစ်ပါပြီ။")
                    st.rerun()
                except Exception as exc:
                    pipeline_status.update(label="Export မအောင်မြင်ပါ", state="error", expanded=True)
                    st.error(f"Video export မအောင်မြင်ပါ: {api_error_message(exc)}")

        final_video_data = st.session_state.get("output_video")
        final_audio_data = st.session_state.get("audio")
        final_srt_text = normalize_srt_text(st.session_state.get("generated_srt", ""))
        if final_video_data:
            if isinstance(final_video_data, bytearray):
                final_video_data = bytes(final_video_data)
            if isinstance(final_video_data, (bytes, memoryview)):
                with st.container(border=True):
                    st.markdown("<div class='final-result-title'>Final Video အဆင်သင့်</div>", unsafe_allow_html=True)
                    st.markdown("<div class='final-result-copy'>Video ဖိုင်ကိုအရင်ဒေါင်းပါ။ ကာဗာပုံကိုအောက်မှာသီးသန့်ထုတ်နိုင်ပါတယ်။</div>", unsafe_allow_html=True)
                    with st.container(border=True):
                        st.markdown("<div class='final-preview-note'>Final Video</div>", unsafe_allow_html=True)
                        st.video(final_video_data)
                        st.download_button("Final Video ဒေါင်းရန်", final_video_data, file_name=f"movie-recap-{st.session_state.get('target_platform', 'video').lower()}.mp4", mime="video/mp4", use_container_width=True)

                    st.markdown("<div class='final-preview-note'>ကာဗာပုံ Prompt</div>", unsafe_allow_html=True)
                    thumbnail_setting_left, thumbnail_setting_right = st.columns(2, gap="small")
                    with thumbnail_setting_left:
                        thumbnail_ratio = st.selectbox("ကာဗာပုံ Size", THUMBNAIL_RATIO_OPTIONS, index=1, key="thumbnail_ratio")
                    with thumbnail_setting_right:
                        selected_part = st.selectbox("ကာဗာပုံ အပိုင်း", THUMBNAIL_PART_OPTIONS, key="thumbnail_part")
                    if st.button("ကာဗာ Prompt ထုတ်မယ်", type="primary", use_container_width=True, key="generate-post-export-thumbnail-prompt"):
                        try:
                            script_for_prompt = str(st.session_state.get("script", "")).strip()
                            if not script_for_prompt:
                                raise ValueError("Script မရှိသေးပါ။")
                            with st.spinner("ကာဗာ Prompt ပြုလုပ်နေပါတယ်..."):
                                thumbnail_title = generate_thumbnail_title(script_for_prompt)
                            st.session_state.thumbnail_prompt = build_thumbnail_prompt(
                                script_for_prompt,
                                thumbnail_title,
                                thumbnail_ratio,
                                None if selected_part == "မရွေးပါ" else selected_part,
                            )
                            st.session_state.thumbnail_prompt_title = thumbnail_title
                            st.session_state.thumbnail_prompt_error = ""
                            st.rerun()
                        except Exception:
                            st.session_state.thumbnail_prompt_error = "ကာဗာ Prompt မထုတ်နိုင်သေးပါ။ ခဏစောင့်ပြီးပြန်စမ်းပါ။"
                    thumbnail_prompt = st.session_state.get("thumbnail_prompt", "")
                    if thumbnail_prompt:
                        st.caption(st.session_state.get("thumbnail_prompt_title", ""))
                        st.code(thumbnail_prompt, language=None)
                        st.download_button(
                            "Prompt ဒေါင်းရန်",
                            thumbnail_prompt.encode("utf-8"),
                            file_name="movie-recap-thumbnail-prompt.txt",
                            mime="text/plain",
                            use_container_width=True,
                            key="download-thumbnail-prompt",
                        )
                    elif st.session_state.get("thumbnail_prompt_error"):
                        st.caption(st.session_state.get("thumbnail_prompt_error"))
                    download_cols = st.columns(2, gap="small")
                    with download_cols[0]:
                        if final_audio_data:
                            st.download_button("Voiceover WAV ဒေါင်းရန်", pcm_to_wav(final_audio_data), file_name="movie-recap-voiceover.wav", mime="audio/wav", use_container_width=True, key="download-final-voiceover")
                    with download_cols[1]:
                        if final_srt_text:
                            st.download_button("SRT ဒေါင်းရန်", final_srt_text.encode("utf-8"), file_name="movie-recap-burmese.srt", mime="text/plain", use_container_width=True, key="download-final-srt")


def apply_compact_ui_theme() -> None:
    """Keep the approved compact Mg Khant layout while using the established export helpers."""
    st.markdown(
        """
        <style>
        :root { color-scheme: dark; }
        .stApp { background:radial-gradient(circle at 8% 0%,#162653 0%,#090e1b 38%,#050811 100%); color:#edf4ff; }
        [data-testid="stHeader"] { background:transparent; }
        .block-container { max-width:1180px; padding:1rem 1.2rem 2rem; }
        .mk-brand { display:flex; align-items:center; gap:.7rem; margin-bottom:.4rem; }
        .mk-brand-mark { width:42px; height:42px; border-radius:12px; display:grid; place-items:center; background:linear-gradient(135deg,#135cff,#7a35ff); font-size:1.3rem; }
        .mk-brand h1 { margin:0; font-size:1.45rem; letter-spacing:.02em; }
        .mk-brand p { margin:.1rem 0 0; color:#aabbd8; font-size:.78rem; }
        .mk-panel { border:1px solid #294b7c; border-radius:16px; background:linear-gradient(145deg,rgba(20,32,59,.96),rgba(8,14,28,.98)); padding:1rem; box-shadow:0 14px 35px rgba(0,0,0,.32); }
        .mk-title { color:#ffe690; font-weight:800; font-size:.92rem; margin:.1rem 0 .75rem; }
        .mk-hint { color:#b7c8e5; font-size:.78rem; margin:.2rem 0 .7rem; }
        .mk-status { border-radius:10px; padding:.6rem .75rem; background:#102445; border:1px solid #3466a4; color:#d9edff; font-size:.78rem; }
        .mk-editor-note { border-left:3px solid #ffd166; padding:.45rem .65rem; background:#142548; border-radius:8px; color:#ddebff; font-size:.78rem; margin:.3rem 0 .8rem; }
        .mk-result { border:1px solid #3f7fc8; background:#0d1e3b; border-radius:14px; padding:1rem; }
        .stTabs [data-baseweb="tab-list"] { gap:.35rem; background:#0a1326; padding:.35rem; margin-top:.65rem; border:1px solid #33598d; border-radius:12px; }
        .stTabs [data-baseweb="tab"] { flex:1 1 0; justify-content:center; height:2.7rem; min-width:0; padding:0 .5rem; border:1px solid #31567f; border-radius:9px; background:#162846; color:#e6f2ff !important; font-size:.8rem; font-weight:800; white-space:nowrap; }
        .stTabs [data-baseweb="tab"] * { color:inherit !important; }
        .stTabs [aria-selected="true"] { background:linear-gradient(90deg,#ffc857,#ff9966); border-color:#ffe1a0; color:#151b2d !important; }
        .stButton > button { border-radius:10px; border:1px solid #87b8ff; background:linear-gradient(90deg,#9fd2ff,#7c9dff); color:#0b1324 !important; font-weight:800; text-shadow:none; box-shadow:0 5px 16px rgba(74,139,255,.28); }
        .stButton > button:hover { border-color:#fff2b5; background:linear-gradient(90deg,#ffe48e,#ffbd70); color:#201509 !important; }
        div.st-key-compact_menu_button button { min-height:2.65rem; min-width:2.65rem; padding:0; border:1px solid #4c75a9; background:#142a4b; color:#edf6ff !important; font-size:1.18rem; font-weight:900; }
        div.st-key-simple_vip button { min-height:2.65rem; border:1px solid #ccefff; background:linear-gradient(135deg,#86d8ff,#6688ff); color:#09162b !important; font-size:.76rem; font-weight:900; }
        div.st-key-pro_vip button { min-height:2.65rem; border:1px solid #ffe39a; background:linear-gradient(135deg,#ffe081,#f49b52); color:#231506 !important; font-size:.76rem; font-weight:900; }
        div[data-testid="stFileUploader"], [data-testid="stFileUploaderDropzone"] { border-color:#79aaf0 !important; background:#102447 !important; border-radius:12px !important; }
        [data-testid="stFileUploaderDropzone"] * { color:#f1f8ff !important; font-weight:700 !important; }
        div[data-testid="stFileUploader"] button { color:#07101f !important; background:#aee8ff !important; border-color:#d5f7ff !important; font-weight:900 !important; }
        .stApp [data-baseweb="select"] > div, .stApp [data-baseweb="input"] > div, .stApp input, .stApp textarea { background-color:#102447 !important; border-color:#6c9ce0 !important; color:#f4f8ff !important; }
        .stApp [data-baseweb="select"] *, .stApp [data-baseweb="input"] input, .stApp input::placeholder, .stApp textarea::placeholder { color:#f4f8ff !important; opacity:1; }
        .stApp [data-baseweb="select"] > div:hover, .stApp [data-baseweb="input"] > div:hover { border-color:#ffd166 !important; }
        div[data-testid="stSelectbox"] svg { fill:#ffd36d !important; }
        div[data-testid="stColorPicker"] input { background:#102447 !important; color:#f4f8ff !important; border-color:#6c9ce0 !important; }
        [data-testid="stWidgetLabel"] p, [data-testid="stCaptionContainer"], .stCaption { color:#c3d2e9 !important; }
        div[role="dialog"], [data-testid="stDialog"], [data-testid="stModal"] { background:#09152d !important; color:#f5f9ff !important; border:1px solid #5d9de6 !important; }
        div[role="dialog"] *, [data-testid="stDialog"] *, [data-testid="stModal"] * { color:#eef6ff; }
        div[role="dialog"] [data-testid="stCaptionContainer"], div[role="dialog"] .stCaption, div[role="dialog"] [data-testid="stWidgetLabel"] p { color:#cfe5ff !important; opacity:1 !important; }
        div[role="dialog"] [data-baseweb="select"] > div, div[role="dialog"] [data-baseweb="input"] > div, div[role="dialog"] input { background:#122a50 !important; color:#ffffff !important; border-color:#74a8ec !important; }
        div[role="dialog"] [data-baseweb="radio"] label, div[role="dialog"] [data-baseweb="radio"] label * { color:#f5f9ff !important; }
        div[role="dialog"] [data-testid="stAlert"] p { color:#071426 !important; font-weight:800; }
        div[role="dialog"] [data-testid="stFileUploaderDropzone"] { background:#102b52 !important; border-color:#84b9fb !important; }
        button[role="switch"][aria-checked="true"] { background:#2ed39b !important; }
        button[role="switch"][aria-checked="false"] { background:#394966 !important; }
        div[data-baseweb="slider"] div[role="slider"] { background:#ffcb5c !important; border-color:#fff0af !important; }
        .mk-voice { border:1px solid #f2ba52; border-radius:10px; background:#2a2139; color:#fff0b5; padding:.45rem .65rem; font-size:.8rem; margin-top:.55rem; }
        @media (max-width:720px) {
          .block-container { padding:.65rem .75rem 1.4rem; }
          .mk-brand h1 { font-size:1.1rem; }
          .mk-brand p { font-size:.67rem; }
          .stTabs [data-baseweb="tab-list"] { gap:.24rem; padding:.26rem; }
          .stTabs [data-baseweb="tab"] { height:2.52rem; padding:0 .18rem; font-size:.67rem; }
          div.st-key-simple_vip button, div.st-key-pro_vip button { min-height:2.5rem; font-size:.64rem; padding:0 .22rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.dialog("Settings")
def render_compact_menu_dialog() -> None:
    """Open settings on one tap without the unreliable light popover trigger."""
    with st.container(border=True):
        st.markdown("### Settings")
        member = st.session_state.get("current_member") or {}
        plan = member.get("effective_plan", "none")
        st.caption(f"{member.get('display_name', '')} · {str(plan).title()}")
        if plan == "simple":
            st.caption("Gemini API Key ထည့်ရန် အပေါ်က Simple Free Button ကိုနှိပ်ပါ။")
        else:
            st.caption("VIP Plan — 3 min Daily quota သို့မဟုတ် Credits ဖြင့် 30 min အထိထုတ်နိုင်ပါတယ်။")
        if st.button("Log out", use_container_width=True, key="compact_logout"):
            st.logout()
        st.divider()
        with st.expander("Admin Stats"):
            admin_password = st.text_input("Password", type="password", key="compact_admin_password")
            if st.button("Stats ဖွင့်မယ်", use_container_width=True, key="compact_admin_open"):
                st.session_state.admin_unlocked = bool(get_admin_password()) and hmac.compare_digest(admin_password, get_admin_password())
            if st.session_state.get("admin_unlocked"):
                stats = generation_stats()
                st.caption(f"24 နာရီ: {stats['last_24_hours']} · စုစုပေါင်း: {stats['total']}")
        render_member_admin()


@st.dialog("Simple Free · Gemini API Key")
def render_simple_free_key_dialog() -> None:
    """Let Simple Free members enter their own Gemini key without opening the menu."""
    st.caption("ကိုယ်ပိုင် Gemini Free API Key ကိုသာထည့်ပါ။ Key ကို GitHub/URL ထဲမသိမ်းပါ။")
    key_value = st.text_input(
        "Gemini API Key",
        type="password",
        value=st.session_state.get("google_ai_key", ""),
        key="simple_free_direct_api_key",
        placeholder="AQ... or AIza...",
    )
    if key_value.strip():
        st.session_state.google_ai_key = key_value.strip()
    if get_api_key():
        st.success("Key ready")
    if st.button("Session Key ဖျက်မယ်", use_container_width=True, key="simple_free_direct_clear_key"):
        st.session_state.pop("google_ai_key", None)
        st.rerun()


def render_compact_menu() -> None:
    """Use a dark, compact button instead of the native popover's white trigger."""
    if st.button("☰", key="compact_menu_button", help="Settings", use_container_width=True):
        render_compact_menu_dialog()


@st.cache_data(show_spinner=False)
def cached_editor_frame(video_path_text: str, modified_ns: int, frame_seconds: float) -> Image.Image:
    """Decode the chosen paused frame once for responsive drag-and-resize editing."""
    del modified_ns
    return extract_preview_frame(Path(video_path_text), max(0.0, float(frame_seconds))).convert("RGB")


def canvas_dimensions(image: Image.Image, maximum_side: int = 500) -> tuple[int, int]:
    width, height = image.size
    scale = min(1.0, float(maximum_side) / max(1, width, height))
    return max(180, round(width * scale)), max(180, round(height * scale))


def direct_editor_frame_data_url(frame: Image.Image, width: int, height: int) -> str:
    """Encode the paused frame in the component payload so Cloud cannot lose its image."""
    preview = frame.convert("RGB").resize((width, height), Image.Resampling.LANCZOS)
    buffer = BytesIO()
    preview.save(buffer, format="JPEG", quality=88, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def fit_frame_for_export_canvas(frame: Image.Image, output_width: int, output_height: int, background_blur: bool) -> Image.Image:
    """Create a small canvas with the same X/Y percentage geometry as final FFmpeg output."""
    canvas_width, canvas_height = canvas_dimensions(Image.new("RGB", (output_width, output_height)), 500)
    source = frame.convert("RGB")
    if background_blur:
        cover_scale = max(canvas_width / source.width, canvas_height / source.height)
        cover = source.resize((max(canvas_width, round(source.width * cover_scale)), max(canvas_height, round(source.height * cover_scale))), Image.Resampling.LANCZOS)
        left = max(0, (cover.width - canvas_width) // 2)
        top = max(0, (cover.height - canvas_height) // 2)
        base = cover.crop((left, top, left + canvas_width, top + canvas_height)).filter(ImageFilter.GaussianBlur(radius=10))
    else:
        base = Image.new("RGB", (canvas_width, canvas_height), "black")
    fit_scale = min(canvas_width / source.width, canvas_height / source.height)
    fit = source.resize((max(2, round(source.width * fit_scale)), max(2, round(source.height * fit_scale))), Image.Resampling.LANCZOS)
    base.paste(fit, ((canvas_width - fit.width) // 2, (canvas_height - fit.height) // 2))
    return base


def normalize_box(x: float, y: float, width: float, height: float, source_width: int, source_height: int) -> dict[str, int]:
    """Clamp a user-drawn Blur rectangle to valid source-video pixels for FFmpeg crop filters."""
    safe_x = max(0, min(int(round(x)), max(0, source_width - 2)))
    safe_y = max(0, min(int(round(y)), max(0, source_height - 2)))
    safe_width = max(2, min(int(round(width)), source_width - safe_x))
    safe_height = max(2, min(int(round(height)), source_height - safe_y))
    return {"x": safe_x, "y": safe_y, "width": safe_width, "height": safe_height}


def object_center(object_data: dict) -> tuple[float, float]:
    left = float(object_data.get("left", 0) or 0)
    top = float(object_data.get("top", 0) or 0)
    if object_data.get("originX") != "center":
        left += float(object_data.get("width", 0) or 0) * float(object_data.get("scaleX", 1) or 1) / 2
    if object_data.get("originY") != "center":
        top += float(object_data.get("height", 0) or 0) * float(object_data.get("scaleY", 1) or 1) / 2
    return left, top


def source_boxes_from_canvas(canvas_json: dict | None, canvas_width: int, canvas_height: int, source_width: int, source_height: int) -> list[dict[str, int]]:
    if not isinstance(canvas_json, dict):
        return []
    boxes: list[dict[str, int]] = []
    for object_data in canvas_json.get("objects", [])[:3]:
        if object_data.get("type") not in {"rect", "rectangle"}:
            continue
        x = float(object_data.get("left", 0) or 0) * source_width / canvas_width
        y = float(object_data.get("top", 0) or 0) * source_height / canvas_height
        width = float(object_data.get("width", 0) or 0) * float(object_data.get("scaleX", 1) or 1) * source_width / canvas_width
        height = float(object_data.get("height", 0) or 0) * float(object_data.get("scaleY", 1) or 1) * source_height / canvas_height
        boxes.append(normalize_box(x, y, width, height, source_width, source_height))
    return boxes


def blurred_boxes_for_mirror(video_path: Path, masks: list[dict]) -> list[dict]:
    """Translate selected source coordinates when the selected export is horizontally mirrored."""
    dimensions = get_video_dimensions(video_path)
    if not dimensions:
        return [dict(mask) for mask in masks]
    source_width, source_height = dimensions
    mirrored: list[dict] = []
    for mask in masks:
        normalized = normalize_box(mask.get("x", 0), mask.get("y", 0), mask.get("width", 2), mask.get("height", 2), source_width, source_height)
        normalized["x"] = max(0, source_width - normalized["x"] - normalized["width"])
        mirrored.append(normalized)
    return mirrored


def compact_export_signature() -> tuple:
    """Prevent a stale finished MP4 from being presented after editor values change."""
    blur_masks = st.session_state.get("blur_masks") or []
    return (
        st.session_state.get("video_path"),
        st.session_state.get("format_aspect"),
        st.session_state.get("format_type"),
        st.session_state.get("format_quality"),
        st.session_state.get("blur_enabled"),
        tuple(tuple(sorted(mask.items())) for mask in blur_masks if isinstance(mask, dict)),
        st.session_state.get("blur_strength"),
        st.session_state.get("subtitle_enabled"),
        st.session_state.get("subtitle_font"),
        st.session_state.get("subtitle_size"),
        st.session_state.get("subtitle_text_color"),
        st.session_state.get("subtitle_outline_color"),
        st.session_state.get("subtitle_background_mode"),
        st.session_state.get("subtitle_background_color"),
        st.session_state.get("subtitle_background_opacity"),
        st.session_state.get("subtitle_x"),
        st.session_state.get("subtitle_y"),
        st.session_state.get("effect_auto_zoom"),
        st.session_state.get("effect_mirror"),
        st.session_state.get("effect_color_filter"),
        st.session_state.get("effect_pitch_alter"),
        st.session_state.get("effect_background_blur"),
        st.session_state.get("logo_overlay_path"),
        st.session_state.get("logo_position"),
        st.session_state.get("logo_motion"),
        st.session_state.get("moving_logo_text"),
        st.session_state.get("voice_model"),
        st.session_state.get("voice_speed"),
        st.session_state.get("timing_basis"),
    )


def render_paused_frame_editor(video_path: Path, duration_seconds: float) -> None:
    """Render one direct draggable overlay on top of the original paused video frame."""
    blur_enabled = bool(st.session_state.get("blur_enabled", False))
    subtitle_enabled = bool(st.session_state.get("subtitle_enabled", False))
    if not blur_enabled and not subtitle_enabled:
        st.video(str(video_path))
        return
    max_seconds = max(0.0, float(duration_seconds or 0.0))
    frame_time = st.slider("Frame Time", 0.0, max_seconds, min(float(st.session_state.get("editor_frame_time", 0.0)), max_seconds), step=0.1, key="editor_frame_time") if max_seconds > 0 else 0.0
    try:
        modified_ns = video_path.stat().st_mtime_ns
        frame = cached_editor_frame(str(video_path), modified_ns, frame_time).convert("RGB")
    except Exception as exc:
        st.error(f"Frame မထုတ်နိုင်သေးပါ: {exc}")
        return
    source_width, source_height = frame.size
    preview_width, preview_height = canvas_dimensions(frame, maximum_side=590)
    if blur_enabled and not st.session_state.get("blur_masks"):
        st.session_state.blur_masks = [{"x": source_width // 10, "y": source_height * 3 // 4, "width": source_width // 2, "height": max(18, source_height // 8)}]
    subtitle_text = " ".join((first_srt_caption(st.session_state.get("generated_srt", "")) or "စာတန်းထိုး နေရာ").split())[:55]
    editor_scene_key = f"{video_path.stat().st_mtime_ns}:{round(frame_time, 1)}:{int(blur_enabled)}:{int(subtitle_enabled)}"
    editor_persist_key = f"{video_path.stat().st_mtime_ns}:{round(frame_time, 1)}"
    editor_refresh = int(st.session_state.get("direct_editor_refresh", 0))
    st.markdown('<div class="mk-editor-note">မူရင်း Video Frame ပေါ်မှာပါ။ <b>Blur Box နဲ့ Subtitle ကို လက်နဲ့ရွှေ့</b>ပြီး Box အစွန်းကိုဆွဲကာအရွယ်ပြောင်းပါ။</div>', unsafe_allow_html=True)
    editor_result = DIRECT_VIDEO_EDITOR(
        image=direct_editor_frame_data_url(frame, preview_width, preview_height),
        width=preview_width,
        height=preview_height,
        source_width=source_width,
        source_height=source_height,
        scene_key=editor_scene_key,
        persist_key=editor_persist_key,
        refresh_token=editor_refresh,
        blur_enabled=blur_enabled,
        subtitle_enabled=subtitle_enabled,
        blur_masks=st.session_state.get("blur_masks", [])[:3],
        subtitle={"text": subtitle_text, "x": int(st.session_state.get("subtitle_x", 50)), "y": int(st.session_state.get("subtitle_y", 78)), "color": st.session_state.get("subtitle_text_color", "#FFD166"), "size": int(st.session_state.get("subtitle_size", 38))},
        key=f"direct_original_video_editor_{video_path.stat().st_mtime_ns}",
        default=None,
    )
    if isinstance(editor_result, dict):
        if blur_enabled and isinstance(editor_result.get("blur_masks"), list):
            updated_masks = []
            for mask in editor_result["blur_masks"][:3]:
                if isinstance(mask, dict):
                    updated_masks.append(normalize_box(mask.get("x", 0), mask.get("y", 0), mask.get("width", 1), mask.get("height", 1), source_width, source_height))
            if updated_masks:
                st.session_state.blur_masks = updated_masks
        if subtitle_enabled and isinstance(editor_result.get("subtitle"), dict):
            subtitle_point = editor_result["subtitle"]
            st.session_state.subtitle_x = max(0, min(100, round(float(subtitle_point.get("x", 50)))))
            st.session_state.subtitle_y = max(0, min(100, round(float(subtitle_point.get("y", 78)))))
    if blur_enabled:
        controls_a, controls_b = st.columns(2)
        with controls_a:
            if st.button("+ Blur Box", use_container_width=True, disabled=len(st.session_state.get("blur_masks") or []) >= 3, key="compact_add_blur_box"):
                st.session_state.blur_masks.append({"x": source_width // 3, "y": source_height // 3, "width": max(20, source_width // 3), "height": max(18, source_height // 8)})
                st.session_state.direct_editor_refresh = editor_refresh + 1
                st.rerun()
            st.slider("Blur Strength", 2, 40, int(st.session_state.get("blur_strength", 18)), key="blur_strength")
        with controls_b:
            st.selectbox("Mask Style", ["None", "Transparent", "Solid Box"], key="blur_background_style")
            if st.session_state.get("blur_background_style") == "Solid Box":
                st.color_picker("Box Color", "#16B8FF", key="solid_box_color")
    if subtitle_enabled:
        st.caption(f"Subtitle position: X {st.session_state.get('subtitle_x', 50)} · Y {st.session_state.get('subtitle_y', 78)}")


def render_compact_export_progress(placeholder, percent: int, step: str) -> None:
    """Replace the source preview with a large, mobile-friendly export status view."""
    safe_percent = max(2, min(100, int(percent)))
    safe_step = html_lib.escape(str(step or "Video ပြင်နေသည်"))
    phases = [
        ("Script", safe_percent >= 8),
        ("Voiceover", safe_percent >= 35),
        ("Subtitle", safe_percent >= 58),
        ("Final Video", safe_percent >= 82),
    ]
    phase_markup = "".join(
        f'<span class="mk-export-phase {"done" if complete else ""}"><b>{"✓" if complete else "○"}</b> {name}</span>'
        for name, complete in phases
    )
    placeholder.markdown(
        f"""
        <style>
        @keyframes mkExportSpin {{ to {{ transform: rotate(360deg); }} }}
        .mk-export-wait {{ min-height: 330px; border: 1px solid rgba(112,184,255,.34); border-radius: 18px; padding: 28px 18px; background: radial-gradient(circle at 50% 20%, rgba(72,130,255,.28), rgba(7,16,31,.98) 58%); display: flex; flex-direction: column; justify-content: center; align-items: center; text-align: center; box-sizing: border-box; }}
        .mk-export-spinner {{ width: 68px; height: 68px; border-radius: 50%; border: 7px solid rgba(144,203,255,.22); border-top-color: #7ed4ff; border-right-color: #c7a4ff; animation: mkExportSpin .85s linear infinite; box-shadow: 0 0 26px rgba(102,195,255,.45); margin-bottom: 20px; }}
        .mk-export-title {{ color: #f6fbff; font-size: 1.22rem; font-weight: 800; margin-bottom: 8px; }}
        .mk-export-step {{ color: #a9d7ff; font-size: .92rem; min-height: 1.4rem; }}
        .mk-export-percent {{ margin: 18px 0 10px; color: #f7fbff; font-size: 1.05rem; font-weight: 800; }}
        .mk-export-track {{ width: min(340px, 92%); height: 10px; overflow: hidden; border-radius: 999px; background: rgba(240,247,255,.13); }}
        .mk-export-fill {{ width: {safe_percent}%; height: 100%; border-radius: inherit; background: linear-gradient(90deg, #66d8ff, #9484ff, #ff96c7); transition: width .35s ease; }}
        .mk-export-phases {{ width: min(360px, 96%); margin-top: 22px; display: grid; grid-template-columns: 1fr 1fr; gap: 8px; text-align: left; }}
        .mk-export-phase {{ color: #8093b5; font-size: .76rem; }} .mk-export-phase.done {{ color: #c9efff; }} .mk-export-phase b {{ display: inline-block; width: 16px; color: #77d9ff; }}
        </style>
        <div class="mk-export-wait">
          <div class="mk-export-spinner"></div>
          <div class="mk-export-title">Video ထုတ်နေသည်…</div>
          <div class="mk-export-step">{safe_step}</div>
          <div class="mk-export-percent">{safe_percent}%</div>
          <div class="mk-export-track"><div class="mk-export-fill"></div></div>
          <div class="mk-export-phases">{phase_markup}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    public_view = str(st.query_params.get("page", "")).strip().lower()
    if public_view in {"privacy", "terms"}:
        render_public_policy_view(public_view)
        return
    apply_compact_ui_theme()
    member = render_account_gate()
    if "subtitle_toggle_initialized" not in st.session_state:
        st.session_state.subtitle_enabled = False
        st.session_state.subtitle_toggle_initialized = True
    plan_label = "Simple Free" if member.get("effective_plan") == "simple" else PAID_PLAN_OFFERS.get(active_subscription_tier(member), {}).get("label", "VIP")
    credit_balance = member_credit_balance(member)
    menu_col, brand_col, simple_vip_col, pro_vip_col = st.columns([0.56, 2.82, 1.28, 1.12], gap="small")
    with menu_col:
        render_compact_menu()
    with brand_col:
        st.markdown(f'<div class="mk-brand"><div class="mk-brand-mark">▶</div><div><h1>One Team</h1><p>{html_lib.escape(plan_label)} · Credits {credit_balance} · Movie Recap Studio</p></div></div>', unsafe_allow_html=True)
    with simple_vip_col:
        if st.button("Simple Free", key="simple_vip", use_container_width=True):
            render_simple_free_key_dialog()
    with pro_vip_col:
        if st.button("Plans · Credits", key="pro_vip", use_container_width=True):
            render_plan_purchase_dialog(member)

    editor_left, editor_right = st.columns([1.28, 1], gap="medium")
    with editor_left:
        st.markdown('<div class="mk-panel"><div class="mk-title">Video Source</div>', unsafe_allow_html=True)
        upload = st.file_uploader("Upload Video", type=["mp4", "mov", "mkv", "avi", "webm"], label_visibility="collapsed", key="video_file")
        if upload:
            upload_token = f"upload:{upload.name}:{upload.size}"
            if st.session_state.get("last_upload_token") != upload_token:
                activate_video_source(save_upload(upload), upload.name, upload_token)
                st.session_state.last_upload_token = upload_token
        video_path_text = st.session_state.get("video_path")
        preview_placeholder = st.empty()
        if video_path_text:
            video_path = Path(video_path_text)
            size_mb = video_path.stat().st_size / (1024 * 1024) if video_path.exists() else 0
            duration = get_video_duration(video_path) or 0.0
            with preview_placeholder.container():
                render_paused_frame_editor(video_path, duration)
            if st.session_state.get("compact_export_error"):
                st.error(str(st.session_state.pop("compact_export_error")))
            st.markdown(f'<div class="mk-status">{st.session_state.get("video_name", "Video")} · {format_duration(round(duration)) if duration else "--:--"} · {size_mb:.1f} MB</div>', unsafe_allow_html=True)
            if duration > PAID_PLAN_MAX_SECONDS:
                needed_credits = credits_for_duration(duration)
                st.info(f"Long Video · {format_duration(round(duration))} · ဒီ Video ထုတ်ရန် {needed_credits} Credits လိုပါတယ်။ Final MP4 အောင်မြင်မှသာ Credits ဖြတ်မယ်။")
            elif member.get("effective_plan") == "simple":
                st.caption("Simple Free · 3 min အထိ · တစ်နေ့ 1 Final Video · ကိုယ်ပိုင် Gemini API Key")
            else:
                tier = PAID_PLAN_OFFERS.get(active_subscription_tier(member), PAID_PLAN_OFFERS["start"])
                st.caption(f"{tier['label']} · 3 min အထိ · တစ်နေ့ {tier['daily_limit']} Final Videos")
        else:
            duration = 0.0
            st.markdown('<div class="mk-status">Local video file တင်ပြီး Preview ကို ဒီမှာကြည့်ပါ။</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    with editor_right:
        st.markdown('<div class="mk-panel"><div class="mk-title">Quick Controls</div>', unsafe_allow_html=True)
        st.toggle("Blur", value=bool(st.session_state.get("blur_enabled", False)), key="blur_enabled")
        st.toggle("Subtitle", value=bool(st.session_state.get("subtitle_enabled", False)), key="subtitle_enabled")
        st.markdown('<div class="mk-hint">ဖွင့်ထားတဲ့ feature ကို Video Frame ပေါ်မှာ လက်နဲ့ချိန်နိုင်ပါတယ်။</div>', unsafe_allow_html=True)
        if video_path_text and st.session_state.get("subtitle_enabled", False):
            with st.expander("Subtitle Style", expanded=True):
                style_a, style_b = st.columns(2)
                with style_a:
                    st.selectbox("Font", list(SUBTITLE_FONT_OPTIONS), key="subtitle_font")
                    st.color_picker("Subtitle Color", st.session_state.get("subtitle_text_color", "#FFD166"), key="subtitle_text_color")
                    st.selectbox("Background", ["Transparent", "Solid background"], key="subtitle_background_mode")
                with style_b:
                    st.slider("Size", 12, 72, int(st.session_state.get("subtitle_size", 38)), key="subtitle_size")
                    st.color_picker("Outline", st.session_state.get("subtitle_outline_color", "#000000"), key="subtitle_outline_color")
                    st.color_picker("Background Color", st.session_state.get("subtitle_background_color", "#000000"), key="subtitle_background_color")
                    st.slider("Background Opacity", 0, 100, int(st.session_state.get("subtitle_background_opacity", 65)), key="subtitle_background_opacity")
        st.markdown('</div>', unsafe_allow_html=True)

    tab_format, tab_edit, tab_logo, tab_voice = st.tabs(["Format", "Edit", "Logo", "Voice"])
    with tab_format:
        st.markdown('<div class="mk-panel">', unsafe_allow_html=True)
        format_a, format_b, format_c = st.columns(3)
        with format_a:
            aspect = st.selectbox("Aspect", ["9:16", "16:9", "1:1"], key="format_aspect")
        with format_b:
            source_kind = st.selectbox("Type", ["Movie Recap", "Simple Movie"], key="format_type")
        with format_c:
            quality_label = st.selectbox("Quality", ["720p", "1080p"], key="format_quality")
        st.markdown('</div>', unsafe_allow_html=True)
    with tab_edit:
        st.markdown('<div class="mk-panel"><div class="mk-hint">လိုအပ်တဲ့ Edit ပဲဖွင့်ပါ။</div>', unsafe_allow_html=True)
        edit_a, edit_b, edit_c = st.columns(3)
        with edit_a:
            st.toggle("Auto Zoom", key="effect_auto_zoom")
            st.toggle("Mirror", key="effect_mirror")
        with edit_b:
            st.toggle("Color Filter", key="effect_color_filter")
            st.toggle("Pitch Alter", key="effect_pitch_alter")
        with edit_c:
            st.toggle("Background Blur", key="effect_background_blur")
            original_audio_ui = st.selectbox("Original Audio", ["Mute All", "Keep Action Sound"], key="original_audio_ui")
        music_upload = st.file_uploader("Background Music", type=["mp3", "wav", "m4a", "aac", "ogg"], key="background_music_upload")
        if music_upload and st.session_state.get("background_music_name") != music_upload.name:
            st.session_state.background_music_path = str(save_upload(music_upload))
            st.session_state.background_music_name = music_upload.name
        if st.session_state.get("background_music_path"):
            st.slider("Music Volume", 0, 35, int(st.session_state.get("background_music_volume", 12)), key="background_music_volume")
        st.session_state.original_audio_mode = "တိုက်ခိုက်သံထား" if original_audio_ui == "Keep Action Sound" else "မူရင်းအသံအကုန်ဖျောက်"
        st.markdown('</div>', unsafe_allow_html=True)
    with tab_logo:
        st.markdown('<div class="mk-panel"><div class="mk-hint">Image Logo တစ်ခုသာ ထည့်နိုင်ပါတယ်။</div>', unsafe_allow_html=True)
        logo_upload = st.file_uploader("Image Logo", type=["png", "jpg", "jpeg", "webp"], key="logo_upload")
        if logo_upload and st.session_state.get("logo_upload_name") != logo_upload.name:
            st.session_state.logo_overlay_path = str(persist_logo_upload(logo_upload))
            st.session_state.logo_upload_name = logo_upload.name
        logo_a, logo_b, logo_c = st.columns([1, 1.45, 1.1])
        with logo_a:
            st.selectbox("Image Position", ["Left", "Right"], key="logo_position")
        with logo_b:
            st.text_input("Text Logo", placeholder="Logo စာ", key="moving_logo_text")
        with logo_c:
            motion_ui = st.selectbox("Text Motion", ["Left", "Right", "Scroll"], key="logo_motion_ui")
        st.session_state.logo_motion = {"Left": "Left static", "Right": "Right static", "Scroll": "Full-screen movement"}[motion_ui]
        st.session_state.text_position = "Bottom center"
        st.markdown('</div>', unsafe_allow_html=True)
    with tab_voice:
        st.markdown('<div class="mk-panel"><div class="mk-hint">အသံကိုရွေးပြီး Speed နဲ့ အချိန်ယူမယ့်ပုံစံကိုချိန်ပါ။</div>', unsafe_allow_html=True)
        voice_tiles = [
            ("🧑‍🎤", "ကိုခန့်", "Aoede"), ("👩‍🎤", "မသီရိ", "Kore"), ("🧑‍🚀", "ကိုမင်း", "Charon"), ("👩‍🚀", "မဝင်း", "Puck"), ("🧑‍🏫", "ကိုဇင်", "Fenrir"),
            ("👩‍🏫", "မသွယ်", "Enceladus"), ("🧑‍💼", "ကိုလင်း", "Orus"), ("👩‍💼", "မေသူ", "Schedar"), ("🧑‍🎨", "ကိုထက်", "Aoede"), ("👩‍🎨", "မနန်း", "Kore"),
        ]
        st.session_state.setdefault("voice_ui", voice_tiles[0][1])
        for start in range(0, len(voice_tiles), 5):
            columns = st.columns(5)
            for column, (avatar, display_name, model_name) in zip(columns, voice_tiles[start:start + 5]):
                with column:
                    if st.button(f"{avatar} {display_name}", key=f"voice_tile_{display_name}", use_container_width=True):
                        st.session_state.voice_ui = display_name
                        st.session_state.voice_model = model_name
        chosen = next((tile for tile in voice_tiles if tile[1] == st.session_state.voice_ui), voice_tiles[0])
        st.session_state.voice_model = chosen[2]
        st.markdown(f'<div class="mk-voice">ရွေးထားသောအသံ: {chosen[0]} {chosen[1]}</div>', unsafe_allow_html=True)
        voice_a, voice_b = st.columns(2)
        with voice_a:
            st.slider("Speed", 0.75, 1.50, float(st.session_state.get("voice_speed", 1.0)), 0.05, key="voice_speed")
        with voice_b:
            st.selectbox("Timing", ["Audio အချိန်", "Video အချိန်"], key="timing_basis")
        st.session_state.voice_style = "cinematic narrator"
        st.markdown('</div>', unsafe_allow_html=True)

    if video_path_text:
        platform = {"9:16": "TikTok", "16:9": "YouTube", "1:1": "Facebook"}[aspect]
        quality_mode = "1280" if quality_label == "1080p" else "720"
        st.session_state.subtitle_export_style = {
            "font": st.session_state.get("subtitle_font", "Noto Sans Myanmar"), "size": st.session_state.get("subtitle_size", 38),
            "text_color": st.session_state.get("subtitle_text_color", "#FFD166"), "outline_color": st.session_state.get("subtitle_outline_color", "#000000"),
            "background_mode": st.session_state.get("subtitle_background_mode", "Transparent"), "background_color": st.session_state.get("subtitle_background_color", "#000000"),
            "background_opacity": st.session_state.get("subtitle_background_opacity", 65), "position": "Bottom",
            "x": st.session_state.get("subtitle_x", 50), "y": st.session_state.get("subtitle_y", 78),
        }
        current_signature = compact_export_signature()
        if st.session_state.get("output_video") and st.session_state.get("compact_output_signature") != current_signature:
            st.session_state.output_video = None
            st.info("ချိန်ညှိချက်ပြောင်းထားလို့ Final Video အသစ်ထုတ်ပါ။")
        st.divider()
        export_label = f"Export Video · {credits_for_duration(duration)} Credits" if duration > PAID_PLAN_MAX_SECONDS else "Export Video"
        if st.button(export_label, type="primary", use_container_width=True, key="compact_export_video"):
            access_error = export_access_error(member, duration)
            if access_error:
                st.warning(access_error)
            elif not duration:
                st.warning("Video အရှည်ကို မဖတ်နိုင်ပါ။ MP4 ဖိုင်တင်ပြီးပြန်စမ်းပါ။")
            else:
                mode = "Faithful full translation" if source_kind == "Movie Recap" else "Original recap"
                render_compact_export_progress(preview_placeholder, 4, "Video Export အတွက် ပြင်ဆင်နေသည်")
                try:
                    def update_progress(percent: int, label: str) -> None:
                        render_compact_export_progress(preview_placeholder, percent, label)
                    final_video, script, final_srt, final_audio = run_one_click_youtube_export(
                        Path(video_path_text), int(duration), "Cinematic and concise", mode, st.session_state.get("voice_model", "Aoede"),
                        st.session_state.get("voice_style", "cinematic narrator"), float(st.session_state.get("voice_speed", 1.0)),
                        bool(st.session_state.get("blur_enabled", False)), st.session_state.get("blur_masks", []), int(st.session_state.get("blur_strength", 18)),
                        st.session_state.get("blur_background_style", "None"), st.session_state.get("solid_box_color", "#16B8FF"),
                        st.session_state.get("subtitle_export_style", {}), st.session_state.get("logo_overlay_path"), st.session_state.get("logo_position", "Right"),
                        st.session_state.get("logo_motion", "Left static"), st.session_state.get("moving_logo_text", ""), st.session_state.get("text_position", "Bottom center"),
                        bool(st.session_state.get("effect_auto_zoom", False)), bool(st.session_state.get("effect_color_filter", False)), bool(st.session_state.get("effect_pitch_alter", False)),
                        subtitle_enabled=bool(st.session_state.get("subtitle_enabled", True)), progress_callback=update_progress,
                        timing_basis=st.session_state.get("timing_basis", "Audio အချိန်"), effect_background_blur=bool(st.session_state.get("effect_background_blur", False)),
                        cached_script=None, cached_audio=None, target_platform=platform, quality_mode=quality_mode,
                        original_audio_mode=st.session_state.get("original_audio_mode", "မူရင်းအသံအကုန်ဖျောက်"),
                        background_music_path=st.session_state.get("background_music_path"), background_music_volume=int(st.session_state.get("background_music_volume", 0)),
                    )
                    st.session_state.script = script
                    st.session_state.generated_srt = final_srt
                    st.session_state.subtitle_srt_editor = final_srt
                    st.session_state.audio = final_audio
                    st.session_state.output_video = final_video
                    st.session_state.compact_output_signature = compact_export_signature()
                    if not register_successful_pro_export(member, duration):
                        st.session_state.output_video = None
                        st.session_state.audio = None
                        st.warning("Quota / Credits ကိုမသိမ်းနိုင်သေးလို့ Final Video ကိုမသိမ်းပါ။ ပြန်စမ်းပါ။")
                        st.stop()
                    register_generation()
                    preview_placeholder.video(final_video)
                    st.success("Final Video, Voice WAV နဲ့ SRT အသင့်ဖြစ်ပါပြီ။")
                except Exception as exc:
                    st.session_state.compact_export_error = f"Video export မအောင်မြင်ပါ: {api_error_message(exc)}"
                    st.rerun()

    final_video = st.session_state.get("output_video")
    if final_video:
        st.markdown('<div class="mk-result"><div class="mk-title">Final Video</div>', unsafe_allow_html=True)
        st.video(final_video)
        st.download_button("Final Video Download", final_video, file_name="one-team-movie-recap.mp4", mime="video/mp4", use_container_width=True)
        download_a, download_b = st.columns(2)
        with download_a:
            if st.session_state.get("audio"):
                st.download_button("Voice WAV Download", pcm_to_wav(st.session_state.audio), file_name="one-team-voiceover.wav", mime="audio/wav", use_container_width=True)
        with download_b:
            final_srt = normalize_srt_text(st.session_state.get("generated_srt", ""))
            if final_srt:
                st.download_button("SRT Download", final_srt.encode("utf-8"), file_name="one-team-burmese.srt", mime="text/plain", use_container_width=True)
        with st.expander("Thumbnail Prompt"):
            thumbnail_ratio = st.selectbox("Size", THUMBNAIL_RATIO_OPTIONS, index=1, key="thumbnail_ratio")
            thumbnail_part = st.selectbox("Part", THUMBNAIL_PART_OPTIONS, key="thumbnail_part")
            if st.button("Prompt ထုတ်မယ်", key="compact_thumbnail_prompt", use_container_width=True):
                try:
                    title = generate_thumbnail_title(st.session_state.get("script", ""))
                    st.session_state.thumbnail_prompt = build_thumbnail_prompt(st.session_state.get("script", ""), title, thumbnail_ratio, None if thumbnail_part == "မရွေးပါ" else thumbnail_part)
                except Exception:
                    st.session_state.thumbnail_prompt = "Prompt မထုတ်နိုင်သေးပါ။ ခဏစောင့်ပြီးပြန်စမ်းပါ။"
            if st.session_state.get("thumbnail_prompt"):
                st.code(st.session_state.thumbnail_prompt, language=None)
                st.download_button("Prompt Download", st.session_state.thumbnail_prompt.encode("utf-8"), file_name="one-team-thumbnail-prompt.txt", mime="text/plain", use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()
