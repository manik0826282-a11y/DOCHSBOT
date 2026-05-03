import logging
import os
import re
import json
import uuid
import random
import string
import asyncio
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode, urlparse, parse_qs
from functools import partial

import requests
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

BOT_TOKEN  = "8770175222:AAGkS09ClydiZtHnExlPKXBCb5Ihs30f0kA"
ADMIN_IDS  = [8009324019]
ADMIN_USERNAME = "@D_OCHS @marufsk07"
DB_DIR     = "user_data"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

executor = ThreadPoolExecutor(max_workers=20)
DB_LOCK  = asyncio.Lock()

os.makedirs(DB_DIR, exist_ok=True)

(
    GET_COOKIES,
    GET_PAGE_ID,
    GET_POST_ID,
    GET_AD_ACCOUNT_ID,
    GET_URL,
    SETTINGS_MENU,
    WAIT_SETTING_INPUT,
    GET_AUTO_URL,
    ADMIN_BROADCAST,
    ADMIN_BROADCAST_PHOTO,
    WAIT_AUTO_REPLY,
) = range(11)

# Country name → code mapping for custom input
COUNTRY_NAME_MAP = {
    "bangladesh": "BD", "india": "IN", "nepal": "NP", "thailand": "TH",
    "kenya": "KE", "south africa": "ZA", "malaysia": "MY", "singapore": "SG",
    "united arab emirates": "AE", "uae": "AE", "saudi arabia": "SA",
    "united states": "US", "usa": "US", "united kingdom": "UK", "uk": "GB",
    "canada": "CA", "australia": "AU", "pakistan": "PK", "germany": "DE",
    "france": "FR", "japan": "JP", "china": "CN", "brazil": "BR",
    "indonesia": "ID", "philippines": "PH", "nigeria": "NG", "egypt": "EG",
    "turkey": "TR", "mexico": "MX", "argentina": "AR", "colombia": "CO",
    "vietnam": "VN", "myanmar": "MM", "sri lanka": "LK", "ghana": "GH",
    "tanzania": "TZ", "ethiopia": "ET", "morocco": "MA", "jordan": "JO",
    "iraq": "IQ", "iran": "IR", "kuwait": "KW", "qatar": "QA",
    "bahrain": "BH", "oman": "OM", "lebanon": "LB", "israel": "IL",
    "spain": "ES", "italy": "IT", "netherlands": "NL", "sweden": "SE",
    "norway": "NO", "denmark": "DK", "poland": "PL", "russia": "RU",
    "ukraine": "UA", "new zealand": "NZ", "south korea": "KR", "taiwan": "TW",
    "hong kong": "HK", "cambodia": "KH", "laos": "LA", "nepal": "NP",
    "maldives": "MV", "bhutan": "BT", "afghanistan": "AF",
    # Region keywords
    "africa": "AFRICA", "african": "AFRICA",
    "asia": "ASIA", "asian": "ASIA",
    "latam": "LATAM", "latin america": "LATAM", "south america": "LATAM",
    "mena": "MENA", "middle east": "MENA",
    "europe": "EUR_REG", "european": "EUR_REG",
}

REGION_CODES = {
    "AFRICA":  {"countries": [],   "country_groups": ["africa"]},
    "AFR":     {"countries": [],   "country_groups": ["africa"]},
    "EUROPE":  {"countries": [],   "country_groups": ["europe"]},
    "EUR_REG": {"countries": [],   "country_groups": ["europe"]},
    "ASIA":    {"countries": [],   "country_groups": ["apac"]},
    "LATAM":   {"countries": [],   "country_groups": ["latam"]},
    "MENA":    {"countries": [],   "country_groups": ["middle_east"]},
}

def build_geo_location(country: str, currency: str = "") -> dict:
    """
    Settings-এ যে Country/Region দেওয়া হবে সেটাই সবসময় use হবে।
    Africa/MENA/ASIA/LATAM region code দিলে country_groups use করবে।
    Currency শুধু billing-এর জন্য, geo location-এ কোনো effect নেই।
    """
    c = country.upper().strip()

    # Region code check (AFRICA, ASIA, LATAM, MENA, EUR_REG)
    if c in REGION_CODES:
        geo = REGION_CODES[c]
        return {
            "countries": geo["countries"],
            "location_types": ["home", "recent"],
            **({"country_groups": geo["country_groups"]} if geo["country_groups"] else {}),
        }

    # Standard country code (BD, US, SA, etc.)
    return {
        "countries": [c],
        "location_types": ["home", "recent"],
    }

def _user_file(user_id: str) -> str:
    return os.path.join(DB_DIR, f"{user_id}.json")

def _load_user_sync(user_id: str) -> dict:
    path = _user_file(user_id)
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_user_sync(user_id: str, data: dict):
    path = _user_file(user_id)
    with open(path, "w") as f:
        json.dump(data, f, indent=4)

async def load_user(user_id: str) -> dict:
    async with DB_LOCK:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(executor, _load_user_sync, user_id)

async def save_user(user_id: str, data: dict):
    async with DB_LOCK:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(executor, _save_user_sync, user_id, data)

def _list_all_users_sync() -> list:
    try:
        return [f[:-5] for f in os.listdir(DB_DIR) if f.endswith(".json")]
    except Exception:
        return []

async def list_all_users() -> list:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, _list_all_users_sync)

def get_default_settings():
    return {"country": "BD", "currency": "USD", "budget": 10.0, "duration": 1}

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

async def get_user_status(user_id: str) -> str:
    data = await load_user(user_id)
    return data.get("status", "new")

async def get_main_keyboard(user_id: int):
    uid    = str(user_id)
    status = await get_user_status(uid)
    admin  = is_admin(user_id)

    if admin:
        return ReplyKeyboardMarkup(
            [["🚀 Boost Link", "💬 Boost Message"],
             ["🔗 POST LINK ADD"],
             ["⚙️ Settings", "🔒 Admin Panel"]],
            resize_keyboard=True,
        )
    elif status == "approved":
        return ReplyKeyboardMarkup(
            [["🚀 Boost Link", "💬 Boost Message"],
             ["🔗 POST LINK ADD"],
             ["⚙️ Settings"]],
            resize_keyboard=True,
        )
    else:
        return ReplyKeyboardMarkup([["📩 Request Access"]], resize_keyboard=True)

async def require_approved(update: Update) -> bool:
    user_id = update.effective_user.id
    if is_admin(user_id):
        return True
    status = await get_user_status(str(user_id))
    if status == "approved":
        return True

    if status == "pending":
        await update.message.reply_text(
            "⏳ Your request is pending.\n\n"
            f"Please wait for admin approval.\nContact: {ADMIN_USERNAME}",
        )
    elif status == "denied":
        await update.message.reply_text(
            "❌ Your access has been denied.\n\n"
            f"You can send /start to request again.\nContact: {ADMIN_USERNAME}",
        )
    else:
        await update.message.reply_text(
            f"⛔ No access. Use /start to request access.\nContact: {ADMIN_USERNAME}",
        )
    return False

async def get_old_keyboard(user_id: str, key: str, btn_text: str, callback_data: str):
    data = await load_user(user_id)
    if data.get(key):
        return InlineKeyboardMarkup([[InlineKeyboardButton(btn_text, callback_data=callback_data)]])
    return None

def get_user_settings(context) -> dict:
    """Always return the user's current settings from context, fallback to defaults."""
    settings = context.user_data.get("settings")
    if not settings:
        settings = get_default_settings()
        context.user_data["settings"] = settings
    return settings

class FacebookSession:
    def __init__(self, cookies_str: str):
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10, pool_maxsize=20,
            max_retries=requests.adapters.Retry(total=2, backoff_factor=0.3),
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://",  adapter)
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.7632.160 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        })

        clean = cookies_str.strip()
        parsed_as_json = False
        try:
            cookie_list = json.loads(clean)
            if isinstance(cookie_list, list):
                parsed_as_json = True
                for c in cookie_list:
                    if isinstance(c, dict) and "name" in c and "value" in c:
                        self.session.cookies.set(c["name"], c["value"], domain=".facebook.com")
        except (json.JSONDecodeError, Exception):
            pass

        if not parsed_as_json:
            clean = clean.replace("\n", "").replace("\r", "")
            if clean.lower().startswith("cookie: "):
                clean = clean[8:]
            for part in clean.split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    self.session.cookies.set(k.strip(), v.strip(), domain=".facebook.com")

        self.tokens = {}

    def _extract_token(self, html, patterns):
        for pattern in patterns:
            m = re.search(pattern, html)
            if m:
                return m.group(1)
        return None

    def extract_all_tokens(self) -> bool:
        try:
            r = self.session.get("https://www.facebook.com/", timeout=15)
            r.raise_for_status()
            html = r.text

            self.tokens["fb_dtsg"] = self._extract_token(html, [r'"DTSGInitialData",\[\],\{"token":"([^"]+)"', r'name="fb_dtsg" value="([^"]+)"'])
            self.tokens["lsd"]     = self._extract_token(html, [r'"LSD",\[\],\{"token":"([^"]+)"', r'name="lsd" value="([^"]+)"'])
            self.tokens["user"]    = self._extract_token(html, [r'"USER_ID":"(\d+)"'])

            if not all([self.tokens.get("fb_dtsg"), self.tokens.get("user")]):
                return False

            num = sum(ord(c) for c in self.tokens["fb_dtsg"])
            self.tokens["jazoest"] = f"2{num}"
            self.tokens["rev"]     = self._extract_token(html, [r'"revision":(\d+)'])
            self.tokens["hsi"]     = self._extract_token(html, [r'"hsi":"([^"]+)"'])
            self.tokens["dyn"]     = self._extract_token(html, [r'"__dyn":"([^"]+)"'])
            self.tokens["spin_r"]  = self._extract_token(html, [r'"__spin_r":(\d+)'])
            self.tokens["spin_b"]  = self._extract_token(html, [r'"__spin_b":"([^"]+)"'])
            self.tokens["spin_t"]  = self._extract_token(html, [r'"__spin_t":(\d+)'])
            self.tokens["hs"]      = self._extract_token(html, [r'"haste_session":"([^"]+)"']) or "20548.HCSV2:comet_pkg.2.1...0"
            return True
        except Exception as e:
            logger.error(f"Token extraction error: {e}")
            return False

    def get_common_params(self, ad_account_id=None):
        def rand_str(n=6):
            return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))
        s = f"{rand_str()}:{rand_str()}:{rand_str()}"
        params = {
            "av": self.tokens.get("user"),
            "__user": self.tokens.get("user"),
            "__a": 1, "__req": "1",
            "__hs": self.tokens.get("hs"), "dpr": 1,
            "__ccg": "MODERATE", "__rev": self.tokens.get("rev"),
            "__s": s, "__hsi": self.tokens.get("hsi"),
            "__dyn": self.tokens.get("dyn"),
            "fb_dtsg": self.tokens.get("fb_dtsg"),
            "jazoest": self.tokens.get("jazoest"),
            "lsd": self.tokens.get("lsd"),
            "__spin_r": self.tokens.get("spin_r"),
            "__spin_b": self.tokens.get("spin_b"),
            "__spin_t": self.tokens.get("spin_t"),
            "__comet_req": "15",
        }
        if ad_account_id:
            params["__aaid"] = ad_account_id
        return params

    def run_boost(self, ad_acc, page, target, website, budget, duration, currency, country):
        try:
            ad_acc_s = ad_acc.replace("act_", "")
            cp       = self.get_common_params(ad_acc_s)
            if not cp.get("fb_dtsg"):
                return {"success": False, "message": "Authentication tokens are missing."}

            budget_val    = round(float(budget) * 100)
            duration_days = int(duration)
            flow_id       = str(uuid.uuid4())

            cta_params = {
                "page_ids[0]": page, "post_ids[0]": target,
                "ad_account_id": ad_acc_s, "source_app_id": "119211728144504",
                "call_to_action_type": "LEARN_MORE",
                "is_from_cta_upgrade_recommendation": "false",
                "call_to_action_link": website, "ads_manager_write_regions": "true",
            }
            cta_url  = f"https://www.facebook.com/ads/existing_post/call_to_action/?{urlencode(cta_params)}"
            cta_res  = self.session.post(cta_url, data=cp, timeout=15)
            cta_text = cta_res.text.replace("for (;;);", "")

            final_story_id  = target if "_" in target else f"{page}_{target}"
            final_target_id = target
            is_success      = False

            try:
                cj = json.loads(cta_text)
                if not cj.get("error") and not cj.get("errors") and (cj.get("payload") or cj.get("success") or cj.get("__ar")):
                    is_success = True
                    pl = cj.get("payload", {})
                    if pl.get("post_id"):
                        final_story_id = pl["post_id"]
                    elif pl.get("id") and isinstance(pl.get("id"), str) and "_" in pl.get("id"):
                        final_story_id = pl["id"]
            except Exception:
                is_success = "error" not in cta_text.lower() and ("success" in cta_text.lower() or "payload" in cta_text.lower())
                m = re.search(r'"(?:post_id|id)"\s*:\s*"(\d+_\d+)"', cta_text)
                if m:
                    final_story_id = m.group(1)

            if "_" in final_story_id:
                final_target_id = final_story_id.split("_")[1]
            else:
                final_target_id = final_story_id
                final_story_id  = f"{page}_{final_target_id}"

            if not is_success and ("error" in cta_text.lower() or "Error" in cta_text):
                final_target_id = target
                final_story_id  = target if "_" in target else f"{page}_{target}"

            geo_loc = build_geo_location(country, "")
            is_region = bool(geo_loc.get("country_groups"))
            targeting_spec = json.dumps({
                "genders": [0], "age_min": 18, "age_max": 65,
                "geo_locations": geo_loc,
                "targeting_optimization": "expansion_all",
                "targeting_automation": {"advantage_audience": 0},
            })
            audience_opt = "NCPP" if is_region else "AUTO_TARGETING"
            dsa_name = str(self.tokens.get("user", ""))

            variables = {"input": {"boost_id": None, "creation_spec": {
                "ab_test_audiences": [{"audience_option": audience_opt, "saved_audience_id": None, "targeting_spec_string": targeting_spec}],
                "ads_lwi_goal": "AUTOMATIC", "audience_option": audience_opt,
                "auto_boost_settings_id": None, "auto_targeting_sources": [],
                "billing_event": "IMPRESSIONS", "budget": budget_val,
                "budget_type": "DAILY_BUDGET", "currency": currency,
                "dayparting_specs": [], "dsa_beneficiary": dsa_name, "dsa_payor": dsa_name,
                "duration_in_days": duration_days, "enable_clo": False,
                "impression_id": str(uuid.uuid4()), "inferred_goal": "GET_WEBSITE_VISITORS",
                "is_automatic_goal": True, "is_budget_flex": False, "is_gen_ai_media": False,
                "is_in_subscription_subsidy": False, "is_instant_ad": False,
                "is_link_click_defaulted_ad": False, "legacy_ad_account_id": ad_acc_s,
                "legacy_entry_point": "www_profile_plus_timeline",
                "logging_spec": {"reach_estimates": {"lower_estimates": 32056, "upper_estimates": 32062}, "spec_history": [{"budget": budget_val, "currency": currency}]},
                "objective": "LINK_CLICKS", "placement_spec": {"publisher_platforms": ["FACEBOOK"]},
                "regional_regulated_categories": [], "regulated_categories": [],
                "regulated_category": "NONE", "retargeting_enabled": False,
                "run_continuously": False, "sabr_version": "v3",
                "saved_audience_id": None, "similar_advertiser_budget_recommendation": 0,
                "similar_advertiser_conversion_count": 0, "special_ad_category_countries": [],
                "start_time": None, "surface": None,
                "targeting_spec_string": targeting_spec, "zero_outcomes_budget_recommendation": 0,
                "adgroup_specs": [{"creative": {
                    "branded_content": {},
                    "call_to_action": {"type": "NO_BUTTON"},
                    "creative_sourcing_spec": {},
                    "degrees_of_freedom_spec": {"creative_features_spec": {"product_extensions": {"action_metadata": {"type": "UNKOWN"}, "enroll_status": "OPT_OUT"}}, "degrees_of_freedom_type": "USER_ENROLLED_LWI_ACO"},
                    "facebook_branded_content": {}, "instagram_branded_content": {},
                    "object_story_id": final_story_id, "use_page_actor_override": None,
                }}],
                "cta_data": None,
                "is_dark_post_flow": False,
            }, "external_dependent_ent_id": None, "flow_id": flow_id,
            "lwi_asset_id": {"id": page}, "manual_review_requested": False,
            "page_id": page, "product": "BOOSTED_POST",
            "target_id": final_target_id, "actor_id": self.tokens.get("user"),
            "client_mutation_id": str(uuid.uuid4())}}

            form_data = {**cp,
                "fb_api_req_friendly_name": "LWICometCreateBoostedComponentMutation",
                "doc_id": "9955578997835249",
                "variables": json.dumps(variables),
            }

            resp = self.session.post("https://www.facebook.com/api/graphql/", data=form_data, timeout=20)
            resp.raise_for_status()
            data = json.loads(resp.text.replace("for (;;);", ""))

            if data.get("errors"):
                return {"success": False, "message": f"Boost Failed: {data['errors'][0].get('message','Unknown error.')}"}

            def get_camp_id(obj):
                if isinstance(obj, dict):
                    if "campaign_id" in obj: return obj["campaign_id"]
                    if "campaign" in obj and "id" in obj["campaign"]: return obj["campaign"]["id"]
                    for k in obj:
                        r2 = get_camp_id(obj[k])
                        if r2: return r2
                elif isinstance(obj, list):
                    for item in obj:
                        r2 = get_camp_id(item)
                        if r2: return r2
                return None

            campaign_id = get_camp_id(data) or flow_id[:8].upper()
            return {"success": True, "campaign_id": campaign_id}

        except requests.RequestException as e:
            return {"success": False, "message": f"Network Error: {e}"}
        except Exception as e:
            logger.error(f"Boost error: {e}", exc_info=True)
            return {"success": False, "message": f"Unexpected error: {e}"}

    def run_massage_boost(self, ad_acc, page, target, budget, duration, currency, country, auto_reply=None):
        try:
            ad_acc_s      = ad_acc.replace("act_", "")
            cp            = self.get_common_params(ad_acc_s)
            if not cp.get("fb_dtsg"):
                return {"success": False, "message": "Authentication tokens are missing."}

            budget_val    = round(float(budget) * 100)
            duration_days = int(duration)
            flow_id       = str(uuid.uuid4())
            story_id      = target if "_" in target else f"{page}_{target}"

            geo_loc = build_geo_location(country, "")
            is_region = bool(geo_loc.get("country_groups"))
            targeting_spec = json.dumps({
                "genders": [1], "age_min": 18, "age_max": 65,
                "geo_locations": geo_loc,
                "targeting_optimization": "expansion_all",
                "targeting_automation": {"advantage_audience": 0},
            })
            audience_opt = "NCPP" if is_region else "AUTO_TARGETING"
            dsa_name = str(self.tokens.get("user", ""))

            variables = {"input": {"boost_id": None, "creation_spec": {
                "ab_test_audiences": [{"audience_option": audience_opt, "saved_audience_id": None, "targeting_spec_string": targeting_spec}],
                "ads_lwi_goal": "GET_MULTI_MESSAGES", "audience_option": audience_opt,
                "auto_boost_settings_id": None, "auto_targeting_sources": [],
                "billing_event": "IMPRESSIONS", "budget": budget_val,
                "budget_type": "DAILY_BUDGET", "currency": currency,
                "dayparting_specs": [] if is_region else [{
                    "days": [1, 2, 3, 4, 5, 6],
                    "end_minute": 1080,
                    "start_minute": 540,
                    "timezone_type": "ADVERTISER_TIME_ZONE",
                }], "dsa_beneficiary": dsa_name, "dsa_payor": dsa_name,
                "duration_in_days": -1 if is_region else duration_days, "enable_clo": False,
                "impression_id": str(uuid.uuid4()), "is_automatic_goal": False,
                "is_budget_flex": False, "is_gen_ai_media": False,
                "is_in_subscription_subsidy": False, "is_instant_ad": False,
                "is_link_click_defaulted_ad": False, "legacy_ad_account_id": ad_acc_s,
                "legacy_entry_point": "www_profile_plus_timeline",
                "logging_spec": {"reach_estimates": {"lower_estimates": 1514, "upper_estimates": 1520}, "spec_history": [{"budget": budget_val, "currency": currency}]},
                "messenger_welcome_message": {
                    "ai_generated_icebreaker_toggle_enabled": False,
                    "automated_greeting_message_cta": "None",
                    "automated_greeting_message_url": "https://",
                    "call_prompt_message": "Call now for faster service.",
                    "greeting": auto_reply if auto_reply else "Hello! How can I help you?",
                    "ice_breakers_edited": True,
                    "icebreakers": ["Where are you located?", "Can you check the price?", "Can I make a purchase?"],
                    "icebreakers_enabled": True,
                    "is_call_prompt_enabled": False,
                    "lwi_web_ai_generated_icebreakers_enabled": False,
                    "prefill": auto_reply if auto_reply else "Hello! Can I get more info on this?",
                    "prefill_enabled": True if auto_reply else False,
                    "prefill_message_edited": True if auto_reply else False,
                    "responses": [auto_reply, auto_reply, auto_reply] if auto_reply else ["Hi", "How can I help?", "Tell me more"],
                    "welcome_message_edited": True,
                },
                "pacing_type": None if is_region else "day_parting", "partner_app_welcome_message": None,
                "pixel_event_type": None, "pixel_id": None,
                "placement_spec": {"publisher_platforms": ["FACEBOOK"]},
                "regional_regulated_categories": [], "regulated_categories": [],
                "regulated_category": "NONE", "retargeting_enabled": False,
                "run_continuously": True, "sabr_version": "v3",
                "saved_audience_id": None, "similar_advertiser_budget_recommendation": 0,
                "similar_advertiser_conversion_count": 0, "special_ad_category_countries": [],
                "start_time": None, "surface": None,
                "targeting_spec_string": targeting_spec, "zero_outcomes_budget_recommendation": 300,
                "adgroup_specs": [{"creative": {
                    "branded_content": {},
                    "call_to_action": {"type": "MESSAGE_PAGE", "value": {"app_destination": "MESSENGER", "link": "https://fb.com/messenger_doc/"}},
                    "creative_sourcing_spec": {},
                    "degrees_of_freedom_spec": {"creative_features_spec": {"product_extensions": {"action_metadata": {"type": "UNKOWN"}, "enroll_status": "OPT_OUT"}}},
                    "facebook_branded_content": {}, "instagram_branded_content": {},
                    "object_story_id": story_id, "use_page_actor_override": None,
                }}],
                "cta_data": {"is_cta_share_post": False, "link": "https://fb.com/messenger_doc/", "type": "MESSAGE_PAGE"},
                "objective": "MESSAGES", "is_dark_post_flow": False,
            }, "external_dependent_ent_id": None, "flow_id": flow_id,
            "lwi_asset_id": {"id": page}, "manual_review_requested": False,
            "page_id": page, "product": "BOOSTED_POST",
            "target_id": target, "actor_id": self.tokens.get("user"),
            "client_mutation_id": str(uuid.uuid4())}}

            form_data = {**cp,
                "__crn": "comet.fbweb.LWICometPostCreationRoute",
                "qpl_active_flow_ids": "584528682",
                "fb_api_analytics_tags": '["qpl_active_flow_ids=584528682"]',
                "fb_api_caller_class": "RelayModern", "server_timestamps": "true",
                "fb_api_req_friendly_name": "LWICometCreateBoostedComponentMutation",
                "doc_id": "9955578997835249", "variables": json.dumps(variables),
            }

            resp = self.session.post("https://www.facebook.com/api/graphql/", data=form_data, timeout=20)
            resp.raise_for_status()
            data = json.loads(resp.text.replace("for (;;);", ""))

            if data.get("errors"):
                return {"success": False, "message": f"Boost Failed: {data['errors'][0].get('message','Unknown error.')}"}

            def get_camp_id(obj):
                if isinstance(obj, dict):
                    if "campaign_id" in obj: return obj["campaign_id"]
                    if "campaign" in obj and "id" in obj["campaign"]: return obj["campaign"]["id"]
                    for k in obj:
                        r2 = get_camp_id(obj[k])
                        if r2: return r2
                elif isinstance(obj, list):
                    for item in obj:
                        r2 = get_camp_id(item)
                        if r2: return r2
                return None

            campaign_id = get_camp_id(data) or flow_id[:8].upper()
            return {"success": True, "campaign_id": campaign_id}

        except requests.RequestException as e:
            return {"success": False, "message": f"Network Error: {e}"}
        except Exception as e:
            logger.error(f"Massage boost error: {e}", exc_info=True)
            return {"success": False, "message": f"Unexpected error: {e}"}

    def add_post_link(self, ad_acc, page, target, website):
        try:
            ad_acc_s = ad_acc.replace("act_", "")
            cp       = self.get_common_params(ad_acc_s)
            if not cp.get("fb_dtsg"):
                return {"success": False, "message": "Authentication tokens are missing."}

            cta_params = {
                "page_ids[0]": page, "post_ids[0]": target,
                "ad_account_id": ad_acc_s, "source_app_id": "119211728144504",
                "call_to_action_type": "LEARN_MORE",
                "is_from_cta_upgrade_recommendation": "false",
                "call_to_action_link": website, "ads_manager_write_regions": "true",
            }
            cta_url  = f"https://www.facebook.com/ads/existing_post/call_to_action/?{urlencode(cta_params)}"
            resp     = self.session.post(cta_url, data=cp, timeout=15)
            cta_text = resp.text.replace("for (;;);", "")

            is_success = False
            try:
                cj = json.loads(cta_text)
                if not cj.get("error") and not cj.get("errors") and (cj.get("payload") or cj.get("success") or cj.get("__ar")):
                    is_success = True
            except Exception:
                is_success = "error" not in cta_text.lower() and ("success" in cta_text.lower() or "payload" in cta_text.lower())

            if is_success or (cta_text and "error" not in cta_text.lower() and "Error" not in cta_text):
                return {"success": True}
            return {"success": False, "message": "Failed to add link. It might already exist or parameters are invalid."}

        except requests.RequestException as e:
            return {"success": False, "message": f"Network Error: {e}"}
        except Exception as e:
            logger.error(f"Add link error: {e}", exc_info=True)
            return {"success": False, "message": f"Unexpected error: {e}"}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    user_id = str(user.id)

    if "settings" not in context.user_data:
        context.user_data["settings"] = get_default_settings()

    if is_admin(user.id):
        keyboard = await get_main_keyboard(user.id)
        await update.message.reply_html(
            rf"👑 <b>Admin Welcome! {user.mention_html()}</b>",
            reply_markup=keyboard,
        )
        return

    udata  = await load_user(user_id)
    status = udata.get("status", "new")

    if status == "approved":
        keyboard = await get_main_keyboard(user.id)
        await update.message.reply_html(
            rf"👋 <b>Hi {user.mention_html()}! Welcome to EASY ADS</b>",
            reply_markup=keyboard,
        )
        return

    if status == "denied":
        udata["status"]   = "pending"
        udata["name"]     = user.full_name
        udata["username"] = f"@{user.username}" if user.username else "N/A"
        await save_user(user_id, udata)
        await _notify_admins(update, context, user)
        await update.message.reply_text(
            "📩 Access request sent!\n\n"
            "Please wait for admin approval.\n"
            f"Contact: {ADMIN_USERNAME}",
            reply_markup=ReplyKeyboardMarkup([["📩 Request Sent..."]], resize_keyboard=True),
        )
        return

    if status == "pending":
        await update.message.reply_text(
            "⏳ Your request is already pending.\n\n"
            "Please wait for admin approval.\n"
            f"Contact: {ADMIN_USERNAME}",
            reply_markup=ReplyKeyboardMarkup([["⏳ Waiting for Approval..."]], resize_keyboard=True),
        )
        return

    udata["status"]   = "pending"
    udata["name"]     = user.full_name
    udata["username"] = f"@{user.username}" if user.username else "N/A"
    await save_user(user_id, udata)

    await _notify_admins(update, context, user)

    await update.message.reply_text(
        "📩 Welcome!\n\n"
        "Your access request has been sent.\n"
        "All features will unlock after admin approval.\n\n"
        f"Contact: {ADMIN_USERNAME}",
        reply_markup=ReplyKeyboardMarkup([["⏳ Waiting for Approval..."]], resize_keyboard=True),
    )

async def _notify_admins(update, context, user):
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user.id}"),
        InlineKeyboardButton("❌ Deny",    callback_data=f"deny_{user.id}"),
    ]])
    text = (
        f"🔔 <b>New Access Request</b>\n\n"
        f"👤 <b>Name:</b> {user.full_name}\n"
        f"🆔 <b>ID:</b> <code>{user.id}</code>\n"
        f"📛 <b>Username:</b> @{user.username or 'N/A'}\n\n"
        f"Please approve or deny:"
    )
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text, reply_markup=keyboard, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")

async def approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    action, target_id = query.data.split("_", 1)

    udata = await load_user(target_id)
    if not udata:
        await query.edit_message_text("⚠️ User not found.")
        return

    name = udata.get("name", "Unknown")

    if action == "approve":
        udata["status"] = "approved"
        await save_user(target_id, udata)
        await query.edit_message_text(
            f"✅ <b>{name}</b> (<code>{target_id}</code>) has been approved.",
            parse_mode="HTML",
        )
        try:
            keyboard = await get_main_keyboard(int(target_id))
            await context.bot.send_message(
                chat_id=int(target_id),
                text="🎉 <b>Your access has been approved!</b>\n\nAll features are now unlocked.",
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.error(f"Could not notify user {target_id}: {e}")

    elif action == "deny":
        udata["status"] = "denied"
        await save_user(target_id, udata)
        await query.edit_message_text(
            f"❌ <b>{name}</b> (<code>{target_id}</code>) has been denied.",
            parse_mode="HTML",
        )
        try:
            await context.bot.send_message(
                chat_id=int(target_id),
                text=(
                    "❌ <b>Your access request has been denied.</b>\n\n"
                    "Use /start to send a new request.\n"
                    f"Contact: {ADMIN_USERNAME}"
                ),
                parse_mode="HTML",
                reply_markup=ReplyKeyboardMarkup([["📩 Request Access"]], resize_keyboard=True),
            )
        except Exception as e:
            logger.error(f"Could not notify user {target_id}: {e}")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    all_ids  = await list_all_users()
    total    = len(all_ids)
    approved = pending = denied = 0

    for uid in all_ids:
        udata  = await load_user(uid)
        status = udata.get("status", "new")
        if status == "approved":   approved += 1
        elif status == "pending":  pending  += 1
        elif status == "denied":   denied   += 1

    text = (
        "🔒 <b>Admin Panel</b>\n\n"
        f"👥 Total Users: {total}\n"
        f"✅ Approved: {approved}\n"
        f"⏳ Pending: {pending}\n"
        f"❌ Denied: {denied}\n\n"
        "Use the buttons below:"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 User List",         callback_data="admin_userlist_0")],
        [InlineKeyboardButton("⏳ Pending Requests",  callback_data="admin_pending")],
        [InlineKeyboardButton("📢 Broadcast Message", callback_data="admin_broadcast_msg")],
        [InlineKeyboardButton("🖼 Broadcast Photo",   callback_data="admin_broadcast_photo")],
    ])
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")
    return ADMIN_BROADCAST  # Keep conversation alive for broadcast states

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    data   = query.data
    caller = update.effective_user.id

    if not is_admin(caller):
        return

    # ── User List ─────────────────────────────────────────
    if data.startswith("admin_userlist_"):
        page    = int(data.split("_")[-1])
        all_ids = await list_all_users()
        start   = page * 5
        chunk   = all_ids[start: start + 5]

        if not chunk:
            await query.edit_message_text("No users found.")
            return

        buttons = []
        for uid in chunk:
            udata  = await load_user(uid)
            status = udata.get("status", "new")
            emoji  = {"approved": "✅", "pending": "⏳", "denied": "❌"}.get(status, "❓")
            name   = udata.get("name", "Unknown")[:15]
            buttons.append([InlineKeyboardButton(f"{emoji} {name} ({uid})", callback_data=f"admin_user_{uid}")])

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin_userlist_{page-1}"))
        if start + 5 < len(all_ids):
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"admin_userlist_{page+1}"))
        if nav:
            buttons.append(nav)
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_back")])

        await query.edit_message_text(
            f"👥 <b>User List</b> (Page {page+1}/{max(1,(len(all_ids)+4)//5)}):",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="HTML",
        )

    # ── Single User Detail ────────────────────────────────
    elif data.startswith("admin_user_"):
        target = data.replace("admin_user_", "")
        udata  = await load_user(target)
        status = udata.get("status", "unknown")
        name   = udata.get("name", "Unknown")
        uname  = udata.get("username", "N/A")

        text = (
            f"👤 <b>{name}</b>\n"
            f"🆔 <code>{target}</code>\n"
            f"📛 {uname}\n"
            f"📊 Status: <b>{status}</b>"
        )
        row1 = []
        if status != "approved":
            row1.append(InlineKeyboardButton("✅ Approve", callback_data=f"approve_{target}"))
        if status != "denied":
            row1.append(InlineKeyboardButton("❌ Deny",    callback_data=f"deny_{target}"))

        keyboard = InlineKeyboardMarkup([
            row1,
            [InlineKeyboardButton("🗑 Remove User", callback_data=f"admin_remove_{target}")],
            [InlineKeyboardButton("🔙 Back",        callback_data="admin_userlist_0")],
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")

    # ── Remove User ───────────────────────────────────────
    elif data.startswith("admin_remove_"):
        target = data.replace("admin_remove_", "")
        udata  = await load_user(target)
        name   = udata.get("name", "Unknown")
        path   = _user_file(target)
        try:
            os.remove(path)
        except Exception:
            pass
        await query.edit_message_text(
            f"🗑 <b>{name}</b> (<code>{target}</code>) has been removed.",
            parse_mode="HTML",
        )
        try:
            await context.bot.send_message(
                chat_id=int(target),
                text=(
                    "⛔ <b>You have been removed from the bot.</b>\n\n"
                    f"Contact: {ADMIN_USERNAME}"
                ),
                parse_mode="HTML",
                reply_markup=ReplyKeyboardMarkup([["📩 Request Access"]], resize_keyboard=True),
            )
        except Exception:
            pass

    # ── Pending Requests ──────────────────────────────────
    elif data == "admin_pending":
        all_ids = await list_all_users()
        pending = []
        for uid in all_ids:
            udata = await load_user(uid)
            if udata.get("status") == "pending":
                pending.append((uid, udata))

        if not pending:
            await query.edit_message_text(
                "✅ No pending requests.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_back")]]),
            )
            return

        buttons = []
        for uid, udata in pending[:10]:
            name = udata.get("name", "Unknown")[:15]
            buttons.append([InlineKeyboardButton(f"⏳ {name} ({uid})", callback_data=f"admin_user_{uid}")])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_back")])

        await query.edit_message_text(
            f"⏳ <b>Pending Requests ({len(pending)})</b>:",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="HTML",
        )

    # ── Broadcast Message — choose count ─────────────────
    elif data == "admin_broadcast_msg":
        context.user_data["broadcast_type"] = "text"
        context.user_data["broadcast_count"] = 999999
        await query.edit_message_text(
            "📢 <b>Broadcast Message</b>\n\n"
            "Type your message below.\n"
            "It will be sent to <b>ALL approved users</b>.\n\n"
            "(Type /cancel to abort)",
            parse_mode="HTML",
        )
        return ADMIN_BROADCAST

    # ── Broadcast Photo ─────────────────────────────────────
    elif data == "admin_broadcast_photo":
        context.user_data["broadcast_type"] = "photo"
        context.user_data["broadcast_count"] = 999999
        await query.edit_message_text(
            "🖼 <b>Broadcast Photo</b>\n\n"
            "Send a photo (with optional caption).\n"
            "It will be sent to <b>ALL approved users</b>.\n\n"
            "(Type /cancel to abort)",
            parse_mode="HTML",
        )
        return ADMIN_BROADCAST_PHOTO

    # ── Back to Admin Home ────────────────────────────────
    elif data == "admin_back":
        all_ids  = await list_all_users()
        total    = len(all_ids)
        approved = pending = denied = 0
        for uid in all_ids:
            udata  = await load_user(uid)
            status = udata.get("status", "new")
            if status == "approved":  approved += 1
            elif status == "pending": pending  += 1
            elif status == "denied":  denied   += 1

        text = (
            "🔒 <b>Admin Panel</b>\n\n"
            f"👥 Total Users: {total}\n"
            f"✅ Approved: {approved}\n"
            f"⏳ Pending: {pending}\n"
            f"❌ Denied: {denied}\n\n"
            "Use the buttons below:"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 User List",         callback_data="admin_userlist_0")],
            [InlineKeyboardButton("⏳ Pending Requests",  callback_data="admin_pending")],
            [InlineKeyboardButton("📢 Broadcast Message", callback_data="admin_broadcast_msg")],
            [InlineKeyboardButton("🖼 Broadcast Photo",   callback_data="admin_broadcast_photo")],
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")

# ── Broadcast count selection callbacks ──────────────────────

async def _get_approved_recipients(limit: int) -> list:
    all_ids = await list_all_users()
    recipients = []
    for uid in all_ids:
        udata = await load_user(uid)
        if udata.get("status") == "approved":
            recipients.append(int(uid))
    if limit < 999999:
        recipients = recipients[:limit]
    return recipients

async def handle_broadcast_photo_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switch to photo broadcast state from inline button."""
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    context.user_data["broadcast_type"] = "photo"
    context.user_data["broadcast_count"] = 999999
    await query.edit_message_text(
        "🖼 <b>Broadcast Photo</b>\n\n"
        "Send a photo (with optional caption).\n"
        "It will be sent to <b>ALL approved users</b>.\n\n"
        "(Type /cancel to abort)",
        parse_mode="HTML",
    )
    return ADMIN_BROADCAST_PHOTO

async def handle_broadcast_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    # /cancel check
    if update.message.text and update.message.text.strip().lower() == "/cancel":
        await update.message.reply_text("🚫 Broadcast cancelled.")
        return ConversationHandler.END

    message    = update.message.text
    admin_id   = update.effective_user.id
    all_ids    = await list_all_users()

    recipients = []
    for uid in all_ids:
        udata = await load_user(uid)
        if udata.get("status") == "approved" and int(uid) != admin_id:
            recipients.append(int(uid))

    total = len(recipients)
    status_msg = await update.message.reply_text(
        f"📢 <b>Broadcasting to {total} users...</b>\n⏳ Please wait...",
        parse_mode="HTML",
    )

    sent = failed = 0
    for i, uid in enumerate(recipients, 1):
        try:
            await context.bot.send_message(chat_id=uid, text=message, parse_mode="HTML")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            failed += 1
            logger.warning(f"Broadcast failed for {uid}: {e}")
        if i % 5 == 0 or i == total:
            try:
                await status_msg.edit_text(
                    f"📢 <b>Broadcasting...</b>\n"
                    f"✅ Sent: {sent} | ❌ Failed: {failed} | 📊 Total: {total}",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    await status_msg.edit_text(
        f"✅ <b>Broadcast Complete!</b>\n\n"
        f"📨 Message sent to all approved users\n"
        f"✅ Sent: <b>{sent}</b>\n"
        f"❌ Failed: <b>{failed}</b>\n"
        f"👥 Total: <b>{total}</b>",
        parse_mode="HTML",
    )
    return ConversationHandler.END

async def handle_broadcast_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    if not update.message.photo:
        await update.message.reply_text("❌ Please send a photo. Type /cancel to abort.")
        return ADMIN_BROADCAST_PHOTO

    photo      = update.message.photo[-1].file_id
    caption    = update.message.caption or ""
    admin_id   = update.effective_user.id
    all_ids    = await list_all_users()
    recipients = []
    for uid in all_ids:
        udata = await load_user(uid)
        if udata.get("status") == "approved" and int(uid) != admin_id:
            recipients.append(int(uid))

    total = len(recipients)
    status_msg = await update.message.reply_text(
        f"🖼 <b>Broadcasting photo to {total} users...</b>\n⏳ Please wait...",
        parse_mode="HTML",
    )

    sent = failed = 0
    for i, uid in enumerate(recipients, 1):
        try:
            await context.bot.send_photo(chat_id=uid, photo=photo, caption=caption, parse_mode="HTML")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            failed += 1
            logger.warning(f"Photo broadcast failed for {uid}: {e}")
        if i % 5 == 0 or i == total:
            try:
                await status_msg.edit_text(
                    f"🖼 <b>Broadcasting...</b>\n"
                    f"✅ Sent: {sent} | ❌ Failed: {failed} | 📊 Total: {total}",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    await status_msg.edit_text(
        f"✅ <b>Broadcast Complete!</b>\n\n"
        f"🖼 Photo sent to all approved users\n"
        f"✅ Sent: <b>{sent}</b>\n"
        f"❌ Failed: <b>{failed}</b>\n"
        f"👥 Total: <b>{total}</b>",
        parse_mode="HTML",
    )
    return ConversationHandler.END

def _create_session_sync(cookies):
    s = FacebookSession(cookies)
    return s if s.extract_all_tokens() else None

async def link_boost_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_approved(update):
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["boost_type"] = "link"
    context.user_data["settings"]   = get_default_settings()
    # Load saved settings from DB and merge
    uid   = str(update.effective_user.id)
    udata = await load_user(uid)
    if udata.get("settings"):
        context.user_data["settings"] = udata["settings"]
    keyboard = await get_old_keyboard(uid, "cookie", "🍪 Use Old Cookies", "old_cookie")
    msg = await update.message.reply_text("🍪 <b>ENTER COOKIES</b>", reply_markup=keyboard, parse_mode="HTML")
    context.user_data["boost_msg_id"] = msg.message_id
    return GET_COOKIES

async def massage_boost_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_approved(update):
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["boost_type"] = "massage"
    context.user_data["settings"]   = get_default_settings()
    uid   = str(update.effective_user.id)
    udata = await load_user(uid)
    if udata.get("settings"):
        context.user_data["settings"] = udata["settings"]
    keyboard = await get_old_keyboard(uid, "cookie", "🍪 Use Old Cookies", "old_cookie")
    msg = await update.message.reply_text("💬 <b>MESSAGE BOOST STARTED</b>\n\n🍪 <b>ENTER COOKIES</b>", reply_markup=keyboard, parse_mode="HTML")
    context.user_data["boost_msg_id"] = msg.message_id
    return GET_COOKIES

async def add_link_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_approved(update):
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["boost_type"] = "add_link"
    context.user_data["settings"]   = get_default_settings()
    uid   = str(update.effective_user.id)
    udata = await load_user(uid)
    if udata.get("settings"):
        context.user_data["settings"] = udata["settings"]
    keyboard = await get_old_keyboard(uid, "cookie", "🍪 Use Old Cookies", "old_cookie")
    msg = await update.message.reply_text("🔗 <b>POST LINK ADD STARTED</b>\n\n🍪 <b>ENTER COOKIES</b>", reply_markup=keyboard, parse_mode="HTML")
    context.user_data["boost_msg_id"] = msg.message_id
    return GET_COOKIES

async def process_cookies_logic(cookies, update, context, chat_id, boost_msg_id, user_id):
    if not boost_msg_id:
        msg = await context.bot.send_message(chat_id=chat_id, text="⏳ <b>Verifying cookies...</b>", parse_mode="HTML")
        context.user_data["boost_msg_id"] = msg.message_id
        boost_msg_id = msg.message_id
    # (already showing "Verifying..." from get_cookies, no need to edit again)

    loop       = asyncio.get_event_loop()
    fb_session = await loop.run_in_executor(executor, partial(_create_session_sync, cookies))

    if fb_session is None:
        keyboard = await get_old_keyboard(user_id, "cookie", "🍪 Use Old Cookies", "old_cookie")
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=boost_msg_id,
            text="❌ <b>Invalid cookies!</b>\n\n🍪 <b>ENTER COOKIES</b>",
            reply_markup=keyboard, parse_mode="HTML",
        )
        return GET_COOKIES

    context.user_data["fb_session"] = fb_session

    udata   = await load_user(user_id)
    buttons = []
    if udata.get("page_id"):
        buttons.append([InlineKeyboardButton("📄 Use Old Page", callback_data="old_page")])
    buttons.append([InlineKeyboardButton("🔗 Auto URL", callback_data="auto_url")])

    await context.bot.edit_message_text(
        chat_id=chat_id, message_id=boost_msg_id,
        text="✅ <b>Cookies verified!</b>\n\n📄 <b>ENTER PAGE ID</b>\n<i>(Or click Auto URL to extract from a link)</i>",
        reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML",
    )
    return GET_PAGE_ID

async def get_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cookies = update.message.text
    user_id = str(update.effective_user.id)

    # Delete cookie message immediately for security
    try:
        await update.message.delete()
    except Exception:
        pass

    # Save to DB
    udata = await load_user(user_id)
    udata["cookie"] = cookies
    await save_user(user_id, udata)

    # Edit the bot's asking message to show "Verifying..."
    boost_msg_id = context.user_data.get("boost_msg_id")
    if boost_msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=boost_msg_id,
                text="⏳ <b>Verifying cookies...</b>",
                parse_mode="HTML",
            )
        except Exception:
            pass

    return await process_cookies_logic(cookies, update, context, update.effective_chat.id, boost_msg_id, user_id)

async def old_cookie_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query   = update.callback_query
    await query.answer("Using old cookies!")
    user_id = str(update.effective_user.id)
    udata   = await load_user(user_id)
    cookies = udata.get("cookie")
    return await process_cookies_logic(cookies, update, context, update.effective_chat.id, context.user_data.get("boost_msg_id"), user_id)

async def process_page_logic(page_id, update, context, chat_id, boost_msg_id):
    context.user_data["page_id"] = page_id
    if boost_msg_id:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=boost_msg_id, text="📝 <b>ENTER POST ID</b>", parse_mode="HTML")
    return GET_POST_ID

async def get_page_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    page_id = update.message.text.strip()
    try:
        await update.message.delete()
    except Exception:
        pass

    user_id = str(update.effective_user.id)
    udata   = await load_user(user_id)
    udata["page_id"] = page_id
    await save_user(user_id, udata)

    return await process_page_logic(page_id, update, context, update.effective_chat.id, context.user_data.get("boost_msg_id"))

async def old_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query   = update.callback_query
    await query.answer("Using old page ID!")
    user_id = str(update.effective_user.id)
    udata   = await load_user(user_id)
    page_id = udata.get("page_id")
    return await process_page_logic(page_id, update, context, update.effective_chat.id, context.user_data.get("boost_msg_id"))

async def auto_url_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query        = update.callback_query
    await query.answer()
    boost_msg_id = context.user_data.get("boost_msg_id")
    if boost_msg_id:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, message_id=boost_msg_id,
            text="🔗 <b>PASTE POST LINK</b>\n<i>(Paste the Facebook post link)</i>",
            parse_mode="HTML",
        )
    return GET_AUTO_URL

async def get_auto_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url_text     = update.message.text.strip()
    boost_msg_id = context.user_data.get("boost_msg_id")
    chat_id      = update.effective_chat.id
    try:
        await update.message.delete()
    except Exception:
        pass

    ad_acc_match = re.search(r'ad_account_id=(\d+)', url_text)
    page_match   = re.search(r'page_id=(\d+)', url_text)
    target_match = re.search(r'(?:target_id|post_id)=(\d+)', url_text)

    ad_acc  = ad_acc_match.group(1) if ad_acc_match else None
    page_id = page_match.group(1) if page_match else None
    post_id = target_match.group(1) if target_match else None

    if not page_id or not post_id:
        parsed = urlparse(url_text)
        path   = parsed.path.strip("/").split("/")
        qs     = parse_qs(parsed.query)

        if not post_id:
            post_id = qs.get("story_fbid", [None])[0]
        if not page_id:
            page_id = qs.get("id", [None])[0]

        if not post_id and len(path) >= 4 and path[-2] == "posts":
            post_id = path[-1]
        if not page_id and len(path) >= 2:
            page_id = path[1]

    if not page_id or not post_id:
        if boost_msg_id:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=boost_msg_id,
                text="❌ <b>Could not extract IDs from link.</b>\n\n📄 <b>Enter Page ID manually:</b>",
                parse_mode="HTML",
            )
        return GET_PAGE_ID

    user_id = str(update.effective_user.id)
    udata   = await load_user(user_id)

    udata["page_id"] = page_id
    udata["post_id"] = post_id
    context.user_data["page_id"] = page_id
    context.user_data["post_id"] = post_id

    if ad_acc:
        udata["ad_account_id"] = ad_acc
        context.user_data["ad_account_id"] = ad_acc
        await save_user(user_id, udata)
        return await execute_after_ad_account(update, context, chat_id, boost_msg_id, user_id)
    else:
        await save_user(user_id, udata)
        if boost_msg_id:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=boost_msg_id,
                text=f"✅ <b>Auto Extracted!</b>\n📄 Page: <code>{page_id}</code>\n📝 Post: <code>{post_id}</code>\n\n💳 <b>ENTER AD ACCOUNT ID</b>",
                parse_mode="HTML",
            )
        return GET_AD_ACCOUNT_ID

async def get_post_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    post_id = update.message.text.strip()
    try:
        await update.message.delete()
    except Exception:
        pass

    context.user_data["post_id"] = post_id
    boost_msg_id = context.user_data.get("boost_msg_id")
    if boost_msg_id:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, message_id=boost_msg_id,
            text="💳 <b>ENTER AD ACCOUNT ID</b>", parse_mode="HTML",
        )
    return GET_AD_ACCOUNT_ID

async def get_ad_account_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    try:
        await update.message.delete()
    except Exception:
        pass

    # Parse multiple ad account IDs — comma, newline, or space separated
    import re as _re
    ad_ids = [x.strip() for x in _re.split(r"[,\s]+", raw) if x.strip()]
    ad_ids = list(dict.fromkeys(ad_ids))  # remove duplicates, keep order

    user_id = str(update.effective_user.id)
    context.user_data["ad_account_ids"] = ad_ids          # list of all IDs
    context.user_data["ad_account_id"]  = ad_ids[0]       # first one (compat)

    udata = await load_user(user_id)
    udata["ad_account_id"]  = ad_ids[0]
    udata["ad_account_ids"] = ad_ids
    await save_user(user_id, udata)

    return await execute_after_ad_account(
        update, context,
        update.effective_chat.id,
        context.user_data.get("boost_msg_id"),
        user_id,
    )

async def execute_after_ad_account(update, context, chat_id, boost_msg_id, user_id):
    boost_type = context.user_data.get("boost_type", "link")
    if boost_type in ["link", "add_link"]:
        keyboard = await get_old_keyboard(user_id, "url", "🔗 Use Old URL", "old_url")
        if boost_msg_id:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=boost_msg_id,
                text="🔗 <b>ENTER URL</b>", reply_markup=keyboard, parse_mode="HTML",
            )
        return GET_URL
    return await execute_massage_boost(context, chat_id, boost_msg_id)

async def execute_massage_boost(context, chat_id, boost_msg_id):
    fb_session: FacebookSession = context.user_data["fb_session"]
    settings   = get_user_settings(context)
    ad_ids     = context.user_data.get("ad_account_ids") or [context.user_data["ad_account_id"]]
    total      = len(ad_ids)

    # Delete the old waiting message
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=boost_msg_id)
    except Exception:
        pass

    # Show pending list
    if total == 1:
        status_msg = await context.bot.send_message(
            chat_id=chat_id,
            text="🚀 <b>Starting Message Boost...</b>",
            parse_mode="HTML",
        )
    else:
        pending_lines = "\n".join([f"  ⏳ <code>{aid}</code>" for aid in ad_ids])
        status_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"╔══════════════════════╗\n"
                f"  🚀 <b>BOOST PENDING</b>\n"
                f"╚══════════════════════╝\n\n"
                f"📋 <b>Ad Accounts ({total}):</b>\n{pending_lines}"
            ),
            parse_mode="HTML",
        )

    loop    = asyncio.get_event_loop()
    results = []

    for i, ad_acc in enumerate(ad_ids, 1):
        result = await loop.run_in_executor(
            executor,
            partial(
                fb_session.run_massage_boost,
                ad_acc     = ad_acc,
                page       = context.user_data["page_id"],
                target     = context.user_data["post_id"],
                budget     = settings["budget"],
                duration   = settings["duration"],
                currency   = settings["currency"],
                country    = settings["country"],
                auto_reply = settings.get("auto_reply") or None,
            ),
        )
        results.append((ad_acc, result))

        # Build updated status
        lines = []
        for j, (aid, res) in enumerate(results):
            if res["success"]:
                lines.append(f"  ✅ <code>{aid}</code> → <code>{res['campaign_id']}</code>")
            else:
                lines.append(f"  ❌ <code>{aid}</code> → {res.get('message','Failed')[:40]}")
        # Remaining pending
        for aid in ad_ids[i:]:
            lines.append(f"  ⏳ <code>{aid}</code>")

        if total > 1:
            done   = sum(1 for _, r in results if r["success"])
            failed = len(results) - done
            status_text = (
                f"╔══════════════════════╗\n"
                f"  🚀 <b>BOOST IN PROGRESS</b>\n"
                f"╚══════════════════════╝\n\n"
                f"📣 <b>Message Boost</b> ({i}/{total})\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                + "\n".join(lines) +
                f"\n━━━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ Done: {done}  ❌ Failed: {failed}"
            )
            try:
                await status_msg.edit_text(status_text, parse_mode="HTML")
            except Exception:
                pass

    # Final summary
    done   = sum(1 for _, r in results if r["success"])
    failed = len(results) - done
    final_lines = []
    for aid, res in results:
        if res["success"]:
            final_lines.append(
                f"╔══════════════════════╗\n"
                f"  ✅ <b>BOOST ACTIVATED</b>\n"
                f"╚══════════════════════╝\n"
                f"📣 <b>Message Boost</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 <b>Campaign ID</b>\n"
                f"   <code>{res['campaign_id']}</code>\n"
                f"💳 <b>Ad Account</b>\n"
                f"   <code>{aid}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🌍 <b>Country  :</b>  <code>{settings['country']}</code>\n"
                f"💱 <b>Currency :</b>  <code>{settings['currency']}</code>\n"
                f"💰 <b>Budget   :</b>  <code>{settings['budget']}</code>\n"
                f"⏳ <b>Duration :</b>  <code>{settings['duration']} days</code>\n"
                f"🚀 <i>Your ad is now live!</i>"
            )
        else:
            final_lines.append(
                f"╔══════════════════════╗\n"
                f"  ❌ <b>BOOST FAILED</b>\n"
                f"╚══════════════════════╝\n"
                f"💳 <b>Ad Account:</b> <code>{aid}</code>\n"
                f"🛑 <b>Reason:</b> {res.get('message','Unknown error')[:100]}"
            )

    # Delete status message
    try:
        await status_msg.delete()
    except Exception:
        pass

    if total == 1:
        # Single ad account: show result directly
        ad_acc, result = results[0]
        if result["success"]:
            msg_text = (
                "╔══════════════════════╗\n"
                "  ✅  <b>BOOST ACTIVATED</b>  ✅\n"
                "╚══════════════════════╝\n\n"
                f"📣 <b>Message Boost</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 <b>Campaign ID</b>\n"
                f"   <code>{result['campaign_id']}</code>\n"
                f"💳 <b>Ad Account</b>\n"
                f"   <code>{ad_acc}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🌍 <b>Country  :</b>  <code>{settings['country']}</code>\n"
                f"💱 <b>Currency :</b>  <code>{settings['currency']}</code>\n"
                f"💰 <b>Budget   :</b>  <code>{settings['budget']}</code>\n"
                f"⏳ <b>Duration :</b>  <code>{settings['duration']} days</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🚀 <i>Your ad is now live!</i>"
            )
        else:
            msg_text = (
                "╔══════════════════════╗\n"
                "  ❌  <b>BOOST FAILED</b>  ❌\n"
                "╚══════════════════════╝\n\n"
                f"💳 <b>Ad Account:</b> <code>{ad_acc}</code>\n"
                f"🛑 <b>Reason:</b>\n{result.get('message','Unknown error')}"
            )
        await context.bot.send_message(chat_id=chat_id, text=msg_text, parse_mode="HTML")
    else:
        # Multi ad account: show summary then individual results
        done   = sum(1 for _, r in results if r["success"])
        failed = sum(1 for _, r in results if not r["success"])
        # Summary header
        summary = (
            "╔══════════════════════╗\n"
            f"  ✅ <b>BOOST COMPLETE</b> — ALL ACTIVE  ✅\n"
            "╚══════════════════════╝\n\n"
            f"📣 <b>Message Boost Results</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
        )
        for ad_acc, result in results:
            if result["success"]:
                summary += f"✅ <code>{ad_acc}</code>\n   🆔 <code>{result['campaign_id']}</code>\n"
            else:
                summary += f"❌ <code>{ad_acc}</code>\n"
        summary += (
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌍 <b>Country  :</b>  <code>{settings['country']}</code>\n"
            f"💱 <b>Currency :</b>  <code>{settings['currency']}</code>\n"
            f"💰 <b>Budget   :</b>  <code>{settings['budget']}</code>\n"
            f"⏳ <b>Duration :</b>  <code>{settings['duration']} days</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Success: <b>{done}</b>  ❌ Failed: <b>{failed}</b>\n"
            f"🚀 <i>Your ads are now live!</i>"
        )
        await context.bot.send_message(chat_id=chat_id, text=summary, parse_mode="HTML")

    context.user_data.pop("fb_session", None)
    return ConversationHandler.END

async def process_url_logic(url, update, context, chat_id, boost_msg_id):
    context.user_data["url"] = url
    boost_type = context.user_data.get("boost_type", "link")
    fb_session: FacebookSession = context.user_data["fb_session"]
    settings   = get_user_settings(context)
    loop       = asyncio.get_event_loop()
    ad_ids     = context.user_data.get("ad_account_ids") or [context.user_data["ad_account_id"]]
    total      = len(ad_ids)

    # Delete waiting message
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=boost_msg_id)
    except Exception:
        pass

    # ── add_link: single operation (no multi needed) ──────
    if boost_type == "add_link":
        status_msg = await context.bot.send_message(
            chat_id=chat_id, text="⏳ <b>Adding link...</b>", parse_mode="HTML"
        )
        result = await loop.run_in_executor(
            executor,
            partial(fb_session.add_post_link,
                ad_acc  = ad_ids[0],
                page    = context.user_data["page_id"],
                target  = context.user_data["post_id"],
                website = url,
            ),
        )
        if result["success"]:
            text = (
                "╔══════════════════════╗\n"
                "  ✅  <b>LINK ADDED</b>  ✅\n"
                "╚══════════════════════╝\n\n"
                f"🔗 <b>Post Link Added Successfully!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💳 <b>Ad Account:</b>  <code>{ad_ids[0]}</code>\n"
                f"📄 <b>Page ID    :</b>  <code>{context.user_data['page_id']}</code>\n"
                f"📝 <b>Post ID    :</b>  <code>{context.user_data['post_id']}</code>"
            )
        else:
            text = (
                "╔══════════════════════╗\n"
                "  ❌  <b>FAILED</b>  ❌\n"
                "╚══════════════════════╝\n\n"
                f"🛑 {result.get('message','')}"
            )
        try:
            await status_msg.delete()
        except Exception:
            pass
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        context.user_data.pop("fb_session", None)
        return ConversationHandler.END

    # ── link boost: multi ad account ─────────────────────
    if total == 1:
        status_msg = await context.bot.send_message(
            chat_id=chat_id,
            text="🚀 <b>Starting Link Boost...</b>",
            parse_mode="HTML",
        )
    else:
        pending_lines = "\n".join([f"  ⏳ <code>{aid}</code>" for aid in ad_ids])
        status_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"╔══════════════════════╗\n"
                f"  🚀 <b>BOOST PENDING</b>\n"
                f"╚══════════════════════╝\n\n"
                f"📋 <b>Ad Accounts ({total}):</b>\n{pending_lines}"
            ),
            parse_mode="HTML",
        )

    results = []
    for i, ad_acc in enumerate(ad_ids, 1):
        result = await loop.run_in_executor(
            executor,
            partial(fb_session.run_boost,
                ad_acc   = ad_acc,
                page     = context.user_data["page_id"],
                target   = context.user_data["post_id"],
                website  = url,
                budget   = settings["budget"],
                duration = settings["duration"],
                currency = settings["currency"],
                country  = settings["country"],
            ),
        )
        results.append((ad_acc, result))

        lines = []
        for j, (aid, res) in enumerate(results):
            if res["success"]:
                lines.append(f"  ✅ <code>{aid}</code> → <code>{res['campaign_id']}</code>")
            else:
                lines.append(f"  ❌ <code>{aid}</code> → {res.get('message','Failed')[:40]}")
        for aid in ad_ids[i:]:
            lines.append(f"  ⏳ <code>{aid}</code>")

        if total > 1:
            done   = sum(1 for _, r in results if r["success"])
            failed = len(results) - done
            try:
                await status_msg.edit_text(
                    f"╔══════════════════════╗\n"
                    f"  🚀 <b>BOOST IN PROGRESS</b>\n"
                    f"╚══════════════════════╝\n\n"
                    f"🔗 <b>Link Boost</b> ({i}/{total})\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    + "\n".join(lines) +
                    f"\n━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"✅ Done: {done}  ❌ Failed: {failed}",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    # Delete status
    try:
        await status_msg.delete()
    except Exception:
        pass

    if total == 1:
        ad_acc, result = results[0]
        if result["success"]:
            msg_text = (
                "╔══════════════════════╗\n"
                "  ✅  <b>BOOST ACTIVATED</b>  ✅\n"
                "╚══════════════════════╝\n\n"
                f"🔗 <b>Link Boost</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 <b>Campaign ID</b>\n"
                f"   <code>{result['campaign_id']}</code>\n"
                f"💳 <b>Ad Account</b>\n"
                f"   <code>{ad_acc}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🌍 <b>Country  :</b>  <code>{settings['country']}</code>\n"
                f"💱 <b>Currency :</b>  <code>{settings['currency']}</code>\n"
                f"💰 <b>Budget   :</b>  <code>{settings['budget']}</code>\n"
                f"⏳ <b>Duration :</b>  <code>{settings['duration']} days</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🚀 <i>Your ad is now live!</i>"
            )
        else:
            msg_text = (
                "╔══════════════════════╗\n"
                "  ❌  <b>BOOST FAILED</b>  ❌\n"
                "╚══════════════════════╝\n\n"
                f"💳 <b>Ad Account:</b> <code>{ad_acc}</code>\n"
                f"🛑 <b>Reason:</b>\n{result.get('message','Unknown error')}"
            )
        await context.bot.send_message(chat_id=chat_id, text=msg_text, parse_mode="HTML")
    else:
        done   = sum(1 for _, r in results if r["success"])
        failed = sum(1 for _, r in results if not r["success"])
        summary = (
            "╔══════════════════════╗\n"
            f"  ✅ <b>BOOST COMPLETE</b> — ALL ACTIVE  ✅\n"
            "╚══════════════════════╝\n\n"
            f"🔗 <b>Link Boost Results</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
        )
        for ad_acc, result in results:
            if result["success"]:
                summary += f"✅ <code>{ad_acc}</code>\n   🆔 <code>{result['campaign_id']}</code>\n"
            else:
                summary += f"❌ <code>{ad_acc}</code>\n"
        summary += (
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌍 <b>Country  :</b>  <code>{settings['country']}</code>\n"
            f"💱 <b>Currency :</b>  <code>{settings['currency']}</code>\n"
            f"💰 <b>Budget   :</b>  <code>{settings['budget']}</code>\n"
            f"⏳ <b>Duration :</b>  <code>{settings['duration']} days</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Success: <b>{done}</b>  ❌ Failed: <b>{failed}</b>\n"
            f"🚀 <i>Your ads are now live!</i>"
        )
        await context.bot.send_message(chat_id=chat_id, text=summary, parse_mode="HTML")

    context.user_data.pop("fb_session", None)
    return ConversationHandler.END

async def get_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url = update.message.text
    try:
        await update.message.delete()
    except Exception:
        pass

    user_id = str(update.effective_user.id)
    udata   = await load_user(user_id)
    udata["url"] = url
    await save_user(user_id, udata)

    return await process_url_logic(url, update, context, update.effective_chat.id, context.user_data.get("boost_msg_id"))

async def old_url_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query   = update.callback_query
    await query.answer("Using old URL!")
    user_id = str(update.effective_user.id)
    udata   = await load_user(user_id)
    url     = udata.get("url")
    return await process_url_logic(url, update, context, update.effective_chat.id, context.user_data.get("boost_msg_id"))

async def show_settings_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_approved(update):
        return ConversationHandler.END

    # Load saved settings from DB so they persist across sessions
    user_id = str(update.effective_user.id)
    udata   = await load_user(user_id)
    if udata.get("settings"):
        context.user_data["settings"] = udata["settings"]
    elif "settings" not in context.user_data:
        context.user_data["settings"] = get_default_settings()

    settings = context.user_data["settings"]
    await update.message.reply_text(
        _get_settings_text(settings),
        reply_markup=InlineKeyboardMarkup(_get_settings_keyboard()),
        parse_mode="HTML",
    )
    return SETTINGS_MENU

async def show_settings_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query    = update.callback_query
    await query.answer()
    settings = context.user_data.get("settings", get_default_settings())
    await query.edit_message_text(
        _get_settings_text(settings),
        reply_markup=InlineKeyboardMarkup(_get_settings_keyboard()),
        parse_mode="HTML",
    )
    return SETTINGS_MENU

def _get_settings_text(settings):
    auto_reply = settings.get("auto_reply", "")
    if auto_reply:
        preview = auto_reply[:80] + "..." if len(auto_reply) > 80 else auto_reply
        ar_text = f"\n💬 <b>Auto Reply:</b> <code>{preview}</code>"
    else:
        ar_text = "\n💬 <b>Auto Reply:</b> <i>Not set</i>"
    return (
        "⚙️ <b>Current Settings</b>\n\n"
        f"🌍 <b>Country:</b> <code>{settings['country']}</code>\n"
        f"💱 <b>Currency:</b> <code>{settings['currency']}</code>\n"
        f"💰 <b>Budget:</b> <code>{settings['budget']}</code>\n"
        f"⏳ <b>Duration:</b> <code>{settings['duration']}</code> days"
        f"{ar_text}\n\n"
        "Click a button below to change a setting."
    )

def _get_settings_keyboard():
    return [
        [InlineKeyboardButton("🌍 Country",  callback_data="set_country"),
         InlineKeyboardButton("💱 Currency", callback_data="set_currency")],
        [InlineKeyboardButton("💰 Budget",   callback_data="set_budget"),
         InlineKeyboardButton("⏳ Duration", callback_data="set_duration")],
        [InlineKeyboardButton("💬 AUTO REPLY", callback_data="set_auto_reply")],
    ]

async def show_country_options(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    # Default 10 countries as requested
    countries = [
        ("🇧🇩 Bangladesh", "BD"), ("🇮🇳 India",         "IN"),
        ("🇳🇵 Nepal",      "NP"), ("🇹🇭 Thailand",       "TH"),
        ("🇰🇪 Kenya",      "KE"), ("🇿🇦 South Africa",   "ZA"),
        ("🇲🇾 Malaysia",   "MY"), ("🇸🇬 Singapore",      "SG"),
        ("🇦🇪 UAE",        "AE"), ("🇸🇦 Saudi Arabia",   "SA"),
    ]
    keyboard = [
        [InlineKeyboardButton(countries[i][0], callback_data=f"save_country_{countries[i][1]}"),
         InlineKeyboardButton(countries[i+1][0], callback_data=f"save_country_{countries[i+1][1]}")]
        for i in range(0, len(countries), 2)
    ]
    # Region options
    keyboard.append([
        InlineKeyboardButton("🌍 Africa Region",  callback_data="save_country_AFRICA"),
        InlineKeyboardButton("🌏 Asia Region",    callback_data="save_country_ASIA"),
    ])
    keyboard.append([
        InlineKeyboardButton("🌎 LatAm Region",   callback_data="save_country_LATAM"),
        InlineKeyboardButton("🕌 MENA Region",    callback_data="save_country_MENA"),
    ])
    keyboard.append([InlineKeyboardButton("✍️ Custom Country/Region", callback_data="custom_country")])
    keyboard.append([InlineKeyboardButton("🔙 Back",                  callback_data="back_to_settings")])
    await query.edit_message_text(
        "🌍 <b>Select Country:</b>\n\n<i>Or click Custom to type any country name</i>",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML",
    )
    return SETTINGS_MENU

async def show_currency_options(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    currencies = [
        ("🇺🇸 USD", "USD"), ("🇪🇺 EUR", "EUR"),
        ("🇬🇧 GBP", "GBP"), ("🇧🇩 BDT", "BDT"),
        ("🇮🇳 INR", "INR"), ("🇸🇦 SAR", "SAR"),
        ("🇦🇪 AED", "AED"), ("🇲🇾 MYR", "MYR"),
        ("🇸🇬 SGD", "SGD"), ("🇰🇪 KES", "KES"),
    ]
    keyboard = [
        [InlineKeyboardButton(currencies[i][0], callback_data=f"save_currency_{currencies[i][1]}"),
         InlineKeyboardButton(currencies[i+1][0], callback_data=f"save_currency_{currencies[i+1][1]}")]
        for i in range(0, len(currencies), 2)
    ]
    keyboard.append([InlineKeyboardButton("✍️ Custom Currency", callback_data="custom_currency")])
    keyboard.append([InlineKeyboardButton("🔙 Back",            callback_data="back_to_settings")])
    await query.edit_message_text(
        "💱 <b>Select Currency:</b>\n\n<i>Or click Custom to type any currency code</i>",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML",
    )
    return SETTINGS_MENU

async def save_from_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if "settings" not in context.user_data:
        context.user_data["settings"] = get_default_settings()

    d = query.data
    if d.startswith("save_country_"):
        context.user_data["settings"]["country"] = d.split("_")[2]
    elif d.startswith("save_currency_"):
        context.user_data["settings"]["currency"] = d.split("_")[2]
    elif d.startswith("save_budget_"):
        context.user_data["settings"]["budget"] = float(d.split("_")[2])
    elif d.startswith("save_duration_"):
        context.user_data["settings"]["duration"] = int(d.split("_")[2])

    # Persist settings to DB immediately
    user_id = str(update.effective_user.id)
    udata   = await load_user(user_id)
    udata["settings"] = context.user_data["settings"]
    await save_user(user_id, udata)

    return await show_settings_edit(update, context)

async def ask_for_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["prompt_msg_id"] = query.message.message_id

    if query.data == "set_budget":
        context.user_data["expected_input"] = "budget"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("$1",  callback_data="save_budget_1"),
             InlineKeyboardButton("$2",  callback_data="save_budget_2"),
             InlineKeyboardButton("$5",  callback_data="save_budget_5")],
            [InlineKeyboardButton("$10", callback_data="save_budget_10"),
             InlineKeyboardButton("$15", callback_data="save_budget_15"),
             InlineKeyboardButton("$20", callback_data="save_budget_20")],
            [InlineKeyboardButton("✍️ Custom (1–1000)", callback_data="custom_budget")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_settings")],
        ])
        await query.edit_message_text(
            "💰 <b>Select Daily Budget:</b>",
            reply_markup=keyboard, parse_mode="HTML",
        )
        return SETTINGS_MENU

    if query.data == "set_duration":
        context.user_data["expected_input"] = "duration"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("1 day",   callback_data="save_duration_1"),
             InlineKeyboardButton("2 days",  callback_data="save_duration_2"),
             InlineKeyboardButton("3 days",  callback_data="save_duration_3")],
            [InlineKeyboardButton("5 days",  callback_data="save_duration_5"),
             InlineKeyboardButton("7 days",  callback_data="save_duration_7"),
             InlineKeyboardButton("14 days", callback_data="save_duration_14")],
            [InlineKeyboardButton("✍️ Custom", callback_data="custom_duration")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_settings")],
        ])
        await query.edit_message_text(
            "⏳ <b>Select Duration:</b>",
            reply_markup=keyboard, parse_mode="HTML",
        )
        return SETTINGS_MENU

    prompts = {
        "custom_country": (
            "🌍 <b>Enter Country Name or Code:</b>\n\n"
            "<i>Name: Bangladesh, India, Saudi Arabia, Africa</i>\n"
            "<i>Code: BD, IN, SA, US, GB</i>"
        ),
        "custom_currency": (
            "💱 <b>Enter Currency Code:</b>\n\n"
            "<i>Examples: USD, BDT, SAR, AED, EUR, MYR</i>"
        ),
        "custom_budget":   (
            "💰 <b>Enter Daily Budget:</b>\n\n"
            "<i>Enter a number between 1 and 10000 (e.g., 10 or 5.5)</i>"
        ),
        "custom_duration": (
            "⏳ <b>Enter Duration in Days:</b>\n\n"
            "<i>Enter a number between 1 and 365 (e.g., 7)</i>"
        ),
    }
    keys = {
        "custom_country":  "country",
        "custom_currency": "currency",
        "custom_budget":   "budget",
        "custom_duration": "duration",
    }
    if query.data not in keys:
        return SETTINGS_MENU
    context.user_data["expected_input"] = keys[query.data]
    context.user_data["prompt_msg_id"]  = query.message.message_id
    await query.edit_message_text(prompts[query.data], parse_mode="HTML")
    return WAIT_SETTING_INPUT

async def handle_setting_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    expected  = context.user_data.get("expected_input")
    user_text = update.message.text.strip()
    chat_id   = update.effective_chat.id
    user_id   = str(update.effective_user.id)

    # Delete user message immediately
    try:
        await update.message.delete()
    except Exception:
        pass

    settings = context.user_data.setdefault("settings", get_default_settings())
    error    = None
    value_set = False

    if expected == "country":
        lookup = user_text.lower().strip()
        if lookup in COUNTRY_NAME_MAP:
            settings["country"] = COUNTRY_NAME_MAP[lookup]
            value_set = True
        elif lookup in REGION_CODES:
            settings["country"] = lookup.upper()
            value_set = True
        elif len(user_text) <= 10 and user_text.replace(" ","").isalpha():
            # Accept any alpha input — 2-letter code or region name
            if len(user_text) == 2:
                settings["country"] = user_text.upper()
            else:
                # Try partial match
                matched = None
                for name, code in COUNTRY_NAME_MAP.items():
                    if lookup in name or name in lookup:
                        matched = code
                        break
                settings["country"] = matched if matched else user_text.upper()[:6]
            value_set = True
        else:
            error = "❌ <b>Invalid country.</b> Enter a country name (e.g., Bangladesh) or 2-letter code (e.g., BD)."

    elif expected == "currency":
        val = user_text.upper().strip().replace(" ","")
        if 2 <= len(val) <= 5 and val.isalpha():
            settings["currency"] = val[:3] if len(val) > 3 else val
            value_set = True
        else:
            error = "❌ <b>Invalid currency.</b> Enter a 3-letter code (e.g., USD, BDT, SAR)."

    elif expected == "budget":
        try:
            val = float(user_text.replace(",","").replace("$",""))
            if 1 <= val <= 10000:
                settings["budget"] = val
                value_set = True
            else:
                error = "❌ <b>Budget must be between 1 and 10000.</b>"
        except ValueError:
            error = "❌ <b>Invalid budget.</b> Enter a number (e.g., 10 or 5.5)."

    elif expected == "duration":
        try:
            val = int(user_text.strip())
            if 1 <= val <= 365:
                settings["duration"] = val
                value_set = True
            else:
                error = "❌ <b>Duration must be between 1 and 365 days.</b>"
        except ValueError:
            error = "❌ <b>Invalid duration.</b> Enter a number (e.g., 7)."

    prompt_msg_id = context.user_data.get("prompt_msg_id")

    if error:
        # Show error and stay in WAIT_SETTING_INPUT
        try:
            if prompt_msg_id:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=prompt_msg_id,
                    text=error + "\n\n<i>Please type again:</i>",
                    parse_mode="HTML",
                )
            else:
                await context.bot.send_message(chat_id=chat_id, text=error, parse_mode="HTML")
        except Exception:
            await context.bot.send_message(chat_id=chat_id, text=error, parse_mode="HTML")
        return WAIT_SETTING_INPUT

    # Save to DB
    udata = await load_user(user_id)
    udata["settings"] = settings
    await save_user(user_id, udata)
    context.user_data["settings"] = settings

    # Show updated settings
    text     = _get_settings_text(settings)
    keyboard = InlineKeyboardMarkup(_get_settings_keyboard())
    try:
        if prompt_msg_id:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=prompt_msg_id,
                text=text, reply_markup=keyboard, parse_mode="HTML",
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id, text=text,
                reply_markup=keyboard, parse_mode="HTML",
            )
    except Exception:
        await context.bot.send_message(
            chat_id=chat_id, text=text,
            reply_markup=keyboard, parse_mode="HTML",
        )
    return SETTINGS_MENU

async def show_auto_reply_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show auto reply management menu."""
    query = update.callback_query
    if query:
        await query.answer()

    settings   = context.user_data.get("settings", {})
    auto_reply = settings.get("auto_reply", "")  # now a single string
    chat_id    = update.effective_chat.id
    msg_id     = query.message.message_id if query else None

    if auto_reply:
        short_preview = auto_reply[:300] + "..." if len(auto_reply) > 300 else auto_reply
        body = (
            "💬 <b>AUTO REPLY Message</b>\n\n"
            f"<code>{short_preview}</code>\n\n"
            "This message will be used as greeting in Message Boost."
        )
    else:
        body = (
            "💬 <b>AUTO REPLY Message</b>\n\n"
            "<i>No auto reply set yet.</i>\n\n"
            "Tap below to add your auto reply message.\n"
            "The entire message will be sent as greeting."
        )

    buttons = []
    if auto_reply:
        buttons.append([InlineKeyboardButton("✏️ Change Auto Reply", callback_data="ar_add")])
        buttons.append([InlineKeyboardButton("🗑 Remove Auto Reply",  callback_data="ar_clear")])
    else:
        buttons.append([InlineKeyboardButton("➕ Add Auto Reply", callback_data="ar_add")])
    buttons.append([InlineKeyboardButton("🔙 Back to Settings", callback_data="back_to_settings")])

    keyboard = InlineKeyboardMarkup(buttons)
    if query and msg_id:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=msg_id,
            text=body, reply_markup=keyboard, parse_mode="HTML",
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id, text=body,
            reply_markup=keyboard, parse_mode="HTML",
        )
    return SETTINGS_MENU

async def auto_reply_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle auto reply add/remove/clear callbacks."""
    query = update.callback_query
    await query.answer()

    user_id  = str(update.effective_user.id)
    settings = context.user_data.setdefault("settings", {})
    if "auto_reply" not in settings:
        settings["auto_reply"] = ""

    data = query.data

    if data == "ar_add":
        await query.edit_message_text(
            "💬 <b>Set Auto Reply Message</b>\n\n"
            "Type your message and send.\n"
            "The entire message will be saved as auto reply.\n\n"
            "<i>Multi-line messages are supported.</i>",
            parse_mode="HTML",
        )
        return WAIT_AUTO_REPLY

    elif data == "ar_clear":
        settings["auto_reply"] = ""
        udata = await load_user(user_id)
        udata["settings"] = settings
        await save_user(user_id, udata)
        context.user_data["settings"] = settings
        return await show_auto_reply_menu(update, context)

    return SETTINGS_MENU

async def handle_auto_reply_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process auto reply - entire message = 1 auto reply message."""
    user_text = update.message.text.strip()

    if not user_text:
        await update.message.reply_text(
            "❌ <b>Empty message.</b> Please type your auto reply message:",
            parse_mode="HTML",
        )
        return WAIT_AUTO_REPLY

    user_id  = str(update.effective_user.id)
    settings = context.user_data.setdefault("settings", {})

    # Entire message = 1 auto reply (replace existing, only 1 allowed)
    settings["auto_reply"] = user_text

    udata = await load_user(user_id)
    udata["settings"] = settings
    await save_user(user_id, udata)
    context.user_data["settings"] = settings

    short_preview = user_text[:200] + "..." if len(user_text) > 200 else user_text
    body = (
        f"✅ <b>Auto Reply Saved!</b>\n\n"
        f"<code>{short_preview}</code>"
    )
    buttons = [
        [InlineKeyboardButton("✏️ Change Auto Reply", callback_data="ar_add")],
        [InlineKeyboardButton("🗑 Remove Auto Reply",  callback_data="ar_clear")],
        [InlineKeyboardButton("🔙 Back to Settings",   callback_data="back_to_settings")],
    ]
    await update.message.reply_text(
        text=body, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML",
    )
    return SETTINGS_MENU

# GROUP POSTER — Facebook Group Post Sharing
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("🚫 <b>Operation cancelled.</b>", parse_mode="HTML")
    else:
        await update.message.reply_text("🚫 <b>Operation cancelled.</b>", parse_mode="HTML")
    context.user_data.pop("fb_session", None)
    return ConversationHandler.END

def main() -> None:
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(10)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(10)
        .build()
    )

    valid_input_filter = (
        filters.TEXT & ~filters.COMMAND
        & ~filters.Regex(
            r"^(🚀 Boost Link|💬 Boost Message|🔗 POST LINK ADD|⚙️ Settings"
            r"|🔒 Admin Panel|📩 Request Access"
            r"|⏳ Waiting for Approval\.\.\.|📩 Request Sent\.\.\.|⏳ Approval waiting\.\.\.)$"
        )
    )

    # Admin broadcast conversation
    admin_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🔒 Admin Panel$"), admin_panel)],
        states={
            ADMIN_BROADCAST: [
                # Allow inline button callbacks to switch to photo state
                CallbackQueryHandler(handle_broadcast_photo_trigger, pattern="^admin_broadcast_photo$"),
                CallbackQueryHandler(admin_callback, pattern="^admin_"),
                CallbackQueryHandler(approval_callback, pattern="^(approve|deny)_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast_text),
            ],
            ADMIN_BROADCAST_PHOTO: [
                CallbackQueryHandler(admin_callback, pattern="^admin_"),
                MessageHandler(filters.PHOTO, handle_broadcast_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: u.message.reply_text("❌ Please send a photo. /cancel to abort.")),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
        conversation_timeout=300,
    )

    # Boost conversation
    boost_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^🚀 Boost Link$"),    link_boost_start),
            MessageHandler(filters.Regex("^💬 Boost Message$"), massage_boost_start),
            MessageHandler(filters.Regex("^🔗 POST LINK ADD$"), add_link_start),
        ],
        states={
            GET_COOKIES: [
                MessageHandler(valid_input_filter, get_cookies),
                CallbackQueryHandler(old_cookie_callback, pattern="^old_cookie$"),
            ],
            GET_PAGE_ID: [
                MessageHandler(valid_input_filter, get_page_id),
                CallbackQueryHandler(old_page_callback, pattern="^old_page$"),
                CallbackQueryHandler(auto_url_callback, pattern="^auto_url$"),
            ],
            GET_POST_ID:       [MessageHandler(valid_input_filter, get_post_id)],
            GET_AD_ACCOUNT_ID: [MessageHandler(valid_input_filter, get_ad_account_id)],
            GET_AUTO_URL:      [MessageHandler(valid_input_filter, get_auto_url)],
            GET_URL: [
                MessageHandler(valid_input_filter, get_url),
                CallbackQueryHandler(old_url_callback, pattern="^old_url$"),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^🚀 Boost Link$"),    link_boost_start),
            MessageHandler(filters.Regex("^💬 Boost Message$"), massage_boost_start),
            MessageHandler(filters.Regex("^🔗 POST LINK ADD$"), add_link_start),
            CallbackQueryHandler(cancel, pattern="^cancel$"),
            CommandHandler("cancel", cancel),
        ],
        allow_reentry=True,
    )

    # Settings conversation
    settings_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^⚙️ Settings$"), show_settings_start)],
        states={
            SETTINGS_MENU: [
                CallbackQueryHandler(show_country_options,  pattern="^set_country$"),
                CallbackQueryHandler(show_currency_options, pattern="^set_currency$"),
                CallbackQueryHandler(ask_for_text_input,    pattern="^(set_budget|set_duration|custom_country|custom_currency|custom_budget|custom_duration)$"),
                CallbackQueryHandler(save_from_button,      pattern="^save_(country|currency|budget|duration)_"),
                CallbackQueryHandler(show_auto_reply_menu,  pattern="^set_auto_reply$"),
                CallbackQueryHandler(auto_reply_callback,   pattern="^ar_"),
                CallbackQueryHandler(show_settings_edit,    pattern="^back_to_settings$"),
            ],
            WAIT_SETTING_INPUT: [MessageHandler(valid_input_filter, handle_setting_input)],
            WAIT_AUTO_REPLY:    [MessageHandler(valid_input_filter, handle_auto_reply_input)],
        },
        fallbacks=[
            CallbackQueryHandler(cancel, pattern="^cancel_settings$"),
            CommandHandler("cancel", cancel),
        ],
        allow_reentry=True,
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(admin_conv)
    application.add_handler(boost_conv)
    application.add_handler(settings_conv)

    # Approval buttons
    application.add_handler(CallbackQueryHandler(approval_callback, pattern="^(approve|deny)_"))

    # Admin panel inline buttons
    application.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))

    logger.info("Bot started")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        timeout=30,
    )

if __name__ == "__main__":
    main()