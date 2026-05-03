import logging
import os
import re
import json
import uuid
import random
import string
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode, urlparse, parse_qs
from functools import partial

import requests
from flask import Flask
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

BOT_TOKEN  = os.environ.get("BOT_TOKEN", "8770175222:AAFIZDLXT62tGdvvZv7RawncJuZdo_M6qnQ")
ADMIN_IDS  = [8009324019]
ADMIN_USERNAME = "@D_OCHS"
DB_DIR     = "user_data"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

executor = ThreadPoolExecutor(max_workers=100)
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
    WAIT_APPROVAL_DAYS,
) = range(12)

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
    "hong kong": "HK", "cambodia": "KH", "laos": "LA",
    "maldives": "MV", "bhutan": "BT", "afghanistan": "AF",
    "africa": "AFRICA", "african": "AFRICA",
    "asia": "ASIA", "asian": "ASIA",
    "latam": "LATAM", "latin america": "LATAM", "south america": "LATAM",
    "mena": "MENA", "middle east": "MENA",
    "europe": "EUR_REG", "european": "EUR_REG",
}

REGION_CODES = {
    "AFRICA":  {"countries": [],  "country_groups": ["africa"]},
    "AFR":     {"countries": [],  "country_groups": ["africa"]},
    "EUROPE":  {"countries": [],  "country_groups": ["europe"]},
    "EUR_REG": {"countries": [],  "country_groups": ["europe"]},
    "ASIA":    {"countries": [],  "country_groups": ["apac"]},
    "APAC":    {"countries": [],  "country_groups": ["apac"]},
    "LATAM":   {"countries": [],  "country_groups": ["latam"]},
    "MENA":    {"countries": [],  "country_groups": ["middle_east"]},
    "WORLDWIDE": {"countries": [], "country_groups": ["africa", "europe", "apac", "latam", "middle_east"]},
}

def build_geo_location(country: str, currency: str = "") -> dict:
    c = country.upper().strip()
    if c in REGION_CODES:
        geo = REGION_CODES[c]
        return {
            "countries": geo["countries"],
            "location_types": ["home", "recent"],
            **({"country_groups": geo["country_groups"]} if geo["country_groups"] else {}),
        }
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

def _expiry_info(udata: dict) -> tuple:
    from datetime import datetime, timezone
    expiry_ts = udata.get("access_expiry")
    if not expiry_ts:
        return False, 999, "Unlimited"
    now  = datetime.now(timezone.utc).timestamp()
    diff = expiry_ts - now
    if diff <= 0:
        return True, 0, datetime.fromtimestamp(expiry_ts).strftime("%Y-%m-%d")
    days_left = int(diff / 86400) + 1
    return False, days_left, datetime.fromtimestamp(expiry_ts).strftime("%Y-%m-%d")

async def check_and_expire_user(user_id: str) -> bool:
    udata = await load_user(user_id)
    if udata.get("status") != "approved":
        return False
    expired, _, _ = _expiry_info(udata)
    if expired:
        udata["status"] = "expired"
        await save_user(user_id, udata)
        return False
    return True

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
    uid = str(user_id)
    udata = await load_user(uid)
    status = udata.get("status", "new")
    if status == "approved":
        expired, _, _ = _expiry_info(udata)
        if expired:
            udata["status"] = "expired"
            await save_user(uid, udata)
            await update.message.reply_text(
                "⏰ <b>Your access has expired.</b>\n\n"
                f"Please contact admin to renew.\nContact: {ADMIN_USERNAME}",
                parse_mode="HTML",
            )
            return False
        return True
    if status == "pending":
        await update.message.reply_text(
            f"⏳ Your request is pending.\n\nContact: {ADMIN_USERNAME}")
    elif status == "expired":
        await update.message.reply_text(
            f"⏰ <b>Your access has expired.</b>\n\nContact: {ADMIN_USERNAME}",
            parse_mode="HTML")
    elif status == "denied":
        await update.message.reply_text(
            f"❌ Your access has been denied.\n\nUse /start to request again.\nContact: {ADMIN_USERNAME}")
    else:
        await update.message.reply_text(
            f"⛔ No access. Use /start to request access.\nContact: {ADMIN_USERNAME}")
    return False

async def get_old_keyboard(user_id: str, key: str, btn_text: str, callback_data: str):
    data = await load_user(user_id)
    if data.get(key):
        return InlineKeyboardMarkup([[InlineKeyboardButton(btn_text, callback_data=callback_data)]])
    return None

def get_user_settings(context) -> dict:
    settings = context.user_data.get("settings")
    if not settings:
        settings = get_default_settings()
        context.user_data["settings"] = settings
    return settings

# ── Anti-detection fingerprint ──────────────────────────
_CHROME_VERSIONS = [
    ("124", "124.0.6367.82"), ("125", "125.0.6422.60"),
    ("126", "126.0.6478.57"), ("127", "127.0.6533.72"),
    ("128", "128.0.6613.85"), ("129", "129.0.6668.70"),
    ("130", "130.0.6723.69"), ("131", "131.0.6778.85"),
    ("132", "132.0.6834.83"), ("133", "133.0.6943.98"),
    ("134", "134.0.6998.88"), ("135", "135.0.7049.84"),
    ("136", "136.0.7103.59"),
]
_PLATFORMS = [
    ("Windows NT 10.0; Win64; x64", "Windows", '"Windows"'),
    ("Windows NT 11.0; Win64; x64", "Windows", '"Windows"'),
    ("Macintosh; Intel Mac OS X 14_6", "macOS", '"macOS"'),
    ("X11; Linux x86_64", "Linux", '"Linux"'),
]
_ACCEPT_LANGS = [
    "en-US,en;q=0.9", "en-US,en;q=0.9,fr;q=0.8",
    "en-GB,en;q=0.9,en-US;q=0.8", "en-US,en;q=0.8,ar;q=0.6",
]
_PIXEL_RATIOS = ["1", "1.25", "1.5", "2"]

def _make_fingerprint() -> dict:
    chrome_major, chrome_full = random.choice(_CHROME_VERSIONS)
    platform_ua, platform_name, platform_js = random.choice(_PLATFORMS)
    lang = random.choice(_ACCEPT_LANGS)
    dpr  = random.choice(_PIXEL_RATIOS)
    ua = (
        f"Mozilla/5.0 ({platform_ua}) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{chrome_full} Safari/537.36"
    )
    sec_ch_ua = (
        f'"Chromium";v="{chrome_major}", '
        f'"Google Chrome";v="{chrome_major}", '
        f'"Not-A.Brand";v="99"'
    )
    return {
        "ua": ua, "sec_ch_ua": sec_ch_ua,
        "sec_ch_ua_platform": platform_js,
        "sec_ch_ua_mobile": "?0",
        "accept_lang": lang, "dpr": dpr,
        "chrome_major": chrome_major,
    }


class FacebookSession:
    def __init__(self, cookies_str: str):
        self.session = requests.Session()
        self._fp = _make_fingerprint()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10, pool_maxsize=20,
            max_retries=requests.adapters.Retry(total=2, backoff_factor=0.3),
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update({
            "User-Agent":      self._fp["ua"],
            "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": self._fp["accept_lang"],
            "Accept-Encoding": "gzip, deflate, br",
            "sec-ch-ua":       self._fp["sec_ch_ua"],
            "sec-ch-ua-mobile":   self._fp["sec_ch_ua_mobile"],
            "sec-ch-ua-platform": self._fp["sec_ch_ua_platform"],
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "max-age=0",
            "Connection": "keep-alive",
        })
        self.tokens = {}
        self._load_cookies(cookies_str)

    def _load_cookies(self, cookies_str: str):
        """
        FIX: Cookies সঠিকভাবে load করা — JSON এবং raw string দুটোই handle করে।
        Cookie session-এ set করার পর clear করে না।
        """
        if not cookies_str:
            logger.warning("Empty cookies string!")
            return

        clean = cookies_str.strip().strip("\"'")

        # JSON array format try করো
        try:
            cookie_list = json.loads(clean)
            if isinstance(cookie_list, list):
                count = 0
                for c in cookie_list:
                    if isinstance(c, dict) and ("name" in c or "key" in c) and "value" in c:
                        name = c.get("name") or c.get("key", "")
                        if name:
                            self.session.cookies.set(name, c["value"], domain=".facebook.com")
                            count += 1
                logger.info(f"Loaded {count} cookies from JSON array")
                return
        except (json.JSONDecodeError, Exception):
            pass

        # Raw cookie string format
        clean = clean.replace("\n", "").replace("\r", "")
        if clean.lower().startswith("cookie: "):
            clean = clean[8:]
        elif clean.lower().startswith("cookie:"):
            clean = clean[7:]

        # ; দিয়ে split করে parse করো
        cookie_pairs = {}
        parts = clean.split(";")
        i = 0
        while i < len(parts):
            part = parts[i].strip()
            # JSON value যদি ; এর ভেতরে থাকে
            while part.count("{") > part.count("}") and i + 1 < len(parts):
                i += 1
                part += ";" + parts[i]
            i += 1
            if "=" in part:
                k, v = part.split("=", 1)
                k = k.strip()
                v = v.strip()
                if k:
                    cookie_pairs[k] = v

        for k, v in cookie_pairs.items():
            self.session.cookies.set(k, v, domain=".facebook.com")

        logger.info(f"Loaded {len(cookie_pairs)} cookies from raw string")

        # Verify গুরুত্বপূর্ণ cookies আছে কিনা
        important = ["c_user", "xs", "datr"]
        found = [c for c in important if any(ck.name == c for ck in self.session.cookies)]
        logger.info(f"Important cookies found: {found}")

    def _extract_token(self, html, patterns):
        for pattern in patterns:
            m = re.search(pattern, html)
            if m:
                return m.group(1)
        return None

    def extract_all_tokens(self) -> dict:
        try:
            r = self.session.get("https://www.facebook.com/", timeout=15)
            r.raise_for_status()
            html = r.text

            if "checkpoint" in r.url or "checkpoint" in html[:500]:
                return {"success": False, "message": "Account is locked (Checkpoint). Login manually."}
            if "/login" in r.url or 'id="email"' in html:
                return {"success": False, "message": "Cookies invalid or expired. Please provide fresh cookies."}

            # c_user cookie থেকে user ID নেওয়া
            c_user = None
            for cookie in self.session.cookies:
                if cookie.name == "c_user":
                    c_user = cookie.value
                    break

            self.tokens["fb_dtsg"] = self._extract_token(html, [
                r'"DTSGInitialData",\[\],\{"token":"([^"]+)"',
                r'"DTSGInitData",\[\],\{"token":"([^"]+)"',
                r'name="fb_dtsg"\s*value="([^"]+)"',
                r'"name":"fb_dtsg","value":"([^"]+)"',
                r'"fb_dtsg":"([^"]+)"',
            ])

            if not self.tokens.get("fb_dtsg"):
                try:
                    r2 = self.session.get("https://business.facebook.com/", timeout=15)
                    if r2.status_code == 200:
                        html2 = r2.text
                        self.tokens["fb_dtsg"] = self._extract_token(html2, [
                            r'"DTSGInitialData",\[\],\{"token":"([^"]+)"',
                            r'"DTSGInitData",\[\],\{"token":"([^"]+)"',
                            r'name="fb_dtsg"\s*value="([^"]+)"',
                            r'"fb_dtsg":"([^"]+)"',
                        ])
                        if self.tokens.get("fb_dtsg"):
                            html = html2
                except Exception:
                    pass

            self.tokens["lsd"] = self._extract_token(html, [
                r'"LSD",\[\],\{"token":"([^"]+)"',
                r'name="lsd" value="([^"]+)"',
            ])
            self.tokens["user"] = self._extract_token(html, [
                r'"USER_ID":"(\d+)"',
                r'"ACCOUNT_ID":"(\d+)"',
                r'"actorID":"(\d+)"',
            ])

            if not self.tokens.get("user"):
                self.tokens["user"] = c_user

            if not all([self.tokens.get("fb_dtsg"), self.tokens.get("user")]):
                return {"success": False, "message": f"Failed to extract tokens. Check cookies validity. URL: {r.url[:60]}"}

            num = sum(ord(c) for c in self.tokens["fb_dtsg"])
            self.tokens["jazoest"] = f"2{num}"
            self.tokens["rev"]     = self._extract_token(html, [r'"revision":(\d+)'])
            self.tokens["hsi"]     = self._extract_token(html, [r'"hsi":"([^"]+)"'])
            self.tokens["dyn"]     = self._extract_token(html, [r'"__dyn":"([^"]+)"'])
            self.tokens["spin_r"]  = self._extract_token(html, [r'"__spin_r":(\d+)'])
            self.tokens["spin_b"]  = self._extract_token(html, [r'"__spin_b":"([^"]+)"'])
            self.tokens["spin_t"]  = self._extract_token(html, [r'"__spin_t":(\d+)'])
            self.tokens["hs"]      = self._extract_token(html, [r'"haste_session":"([^"]+)"']) or "20548.HCSV2:comet_pkg.2.1...0"

            return {"success": True}

        except requests.exceptions.ProxyError:
            return {"success": False, "message": "Proxy Connection Error!"}
        except requests.exceptions.ConnectTimeout:
            return {"success": False, "message": "Connection Timeout!"}
        except requests.exceptions.ConnectionError as e:
            return {"success": False, "message": f"Connection Error: {str(e)[:50]}"}
        except Exception as e:
            logger.error(f"Token extraction error: {e}")
            return {"success": False, "message": f"Session Error: {str(e)[:50]}"}

    def get_common_params(self, ad_account_id=None):
        def rand_str(n=6):
            return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))
        s = f"{rand_str()}:{rand_str()}:{rand_str()}"
        self.session.headers.update({
            "Content-Type":   "application/x-www-form-urlencoded",
            "Origin":         "https://www.facebook.com",
            "Referer":        "https://www.facebook.com/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        })
        ccg_choices = ["EXCELLENT", "GOOD", "MODERATE"]
        params = {
            "av":      self.tokens.get("user"),
            "__user":  self.tokens.get("user"),
            "__a":     1,
            "__req":   str(random.randint(1, 9)),
            "__hs":    self.tokens.get("hs"),
            "dpr":     self._fp["dpr"],
            "__ccg":   random.choice(ccg_choices),
            "__rev":   self.tokens.get("rev"),
            "__s":     s,
            "__hsi":   self.tokens.get("hsi"),
            "__dyn":   self.tokens.get("dyn"),
            "fb_dtsg": self.tokens.get("fb_dtsg"),
            "jazoest": self.tokens.get("jazoest"),
            "lsd":     self.tokens.get("lsd"),
            "__spin_r": self.tokens.get("spin_r"),
            "__spin_b": self.tokens.get("spin_b"),
            "__spin_t": self.tokens.get("spin_t"),
            "__comet_req": "15",
            "fb_api_caller_class": "RelayModern",
            "server_timestamps": "true",
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
                "cta_data": None, "is_dark_post_flow": False,
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
            ad_acc_s = ad_acc.replace("act_", "")
            cp = self.get_common_params(ad_acc_s)
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
                    "days": [1, 2, 3, 4, 5, 6], "end_minute": 1080, "start_minute": 540,
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
                "pacing_type": None if is_region else "day_parting",
                "partner_app_welcome_message": None,
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
            cp = self.get_common_params(ad_acc_s)
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
            return {"success": False, "message": "Failed to add link."}

        except requests.RequestException as e:
            return {"success": False, "message": f"Network Error: {e}"}
        except Exception as e:
            logger.error(f"Add link error: {e}", exc_info=True)
            return {"success": False, "message": f"Unexpected error: {e}"}


def _create_session_sync(cookies):
    """FIX: cookies সঠিকভাবে session-এ রাখে, token extract করে"""
    s = FacebookSession(cookies)
    res = s.extract_all_tokens()
    if isinstance(res, dict):
        if res.get("success"):
            return s, "Success"
        return None, res.get("message", "Invalid cookies!")
    if res:
        return s, "Success"
    return None, "Invalid cookies or session expired!"


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
        expired, days_left, expiry_date = _expiry_info(udata)
        if expired:
            udata["status"] = "expired"
            await save_user(user_id, udata)
            await update.message.reply_text(
                "⏰ <b>Your access has expired.</b>\n\n"
                f"Contact: {ADMIN_USERNAME}",
                parse_mode="HTML",
                reply_markup=ReplyKeyboardMarkup([["📩 Request Renewal"]], resize_keyboard=True),
            )
            return
        if days_left <= 3 and days_left != 999:
            expiry_note = f"\n\n⚠️ <b>Access expires in {days_left} day(s)</b> ({expiry_date})"
        elif expiry_date == "Unlimited":
            expiry_note = ""
        else:
            expiry_note = f"\n\n✅ Valid until: <b>{expiry_date}</b> ({days_left} days left)"
        keyboard = await get_main_keyboard(user.id)
        await update.message.reply_html(
            rf"👋 <b>Hi {user.mention_html()}! Welcome to ⚡ NO TALK • ONLY BOOST 💰</b>" + expiry_note,
            reply_markup=keyboard,
        )
        return

    if status == "expired":
        await update.message.reply_text(
            "⏰ <b>Your access has expired.</b>\n\n"
            f"Contact: {ADMIN_USERNAME}",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup([["📩 Request Renewal"]], resize_keyboard=True),
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
            f"Please wait for admin approval.\nContact: {ADMIN_USERNAME}",
            reply_markup=ReplyKeyboardMarkup([["📩 Request Sent..."]], resize_keyboard=True),
        )
        return

    if status == "pending":
        await update.message.reply_text(
            "⏳ Your request is already pending.\n\n"
            f"Please wait for admin approval.\nContact: {ADMIN_USERNAME}",
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
        context.user_data["approve_target_id"] = target_id
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("1 Day",   callback_data=f"setdays_{target_id}_1"),
             InlineKeyboardButton("3 Days",  callback_data=f"setdays_{target_id}_3"),
             InlineKeyboardButton("7 Days",  callback_data=f"setdays_{target_id}_7")],
            [InlineKeyboardButton("15 Days", callback_data=f"setdays_{target_id}_15"),
             InlineKeyboardButton("30 Days", callback_data=f"setdays_{target_id}_30"),
             InlineKeyboardButton("90 Days", callback_data=f"setdays_{target_id}_90")],
            [InlineKeyboardButton("✍️ Custom Days", callback_data=f"setdays_{target_id}_custom")],
        ])
        await query.edit_message_text(
            f"✅ Approving <b>{name}</b> (<code>{target_id}</code>)\n\n"
            f"⏳ <b>How many days of access?</b>",
            reply_markup=keyboard, parse_mode="HTML",
        )
    elif action == "deny":
        udata["status"] = "denied"
        await save_user(target_id, udata)
        await query.edit_message_text(
            f"❌ <b>{name}</b> (<code>{target_id}</code>) has been denied.", parse_mode="HTML")
        try:
            await context.bot.send_message(
                chat_id=int(target_id),
                text=f"❌ <b>Your access request has been denied.</b>\n\nUse /start to try again.\nContact: {ADMIN_USERNAME}",
                parse_mode="HTML",
                reply_markup=ReplyKeyboardMarkup([["📩 Request Access"]], resize_keyboard=True),
            )
        except Exception as e:
            logger.error(f"Could not notify user {target_id}: {e}")

async def setdays_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return
    parts     = query.data.split("_")
    target_id = parts[1]
    days_str  = parts[2]

    if days_str == "custom":
        context.user_data["approve_target_id"] = target_id
        context.user_data["approve_msg_id"]    = query.message.message_id
        await query.edit_message_text(
            f"✍️ <b>Enter number of days</b> for <code>{target_id}</code>:\n\n"
            "<i>Type a number (1–3650) and send:</i>",
            parse_mode="HTML",
        )
        return WAIT_APPROVAL_DAYS

    await _do_approve_user(
        context=context, bot=context.bot,
        target_id=target_id, days=int(days_str),
        msg_to_edit=query.message,
    )

async def _do_approve_user(context, bot, target_id: str, days: int, msg_to_edit=None):
    from datetime import datetime, timezone, timedelta
    udata = await load_user(target_id)
    name  = udata.get("name", "Unknown")
    udata["status"] = "approved"
    if days > 0:
        expiry_ts = (datetime.now(timezone.utc) + timedelta(days=days)).timestamp()
        udata["access_expiry"] = expiry_ts
        expiry_str = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")
    else:
        udata.pop("access_expiry", None)
        expiry_str = "Unlimited"
    await save_user(target_id, udata)

    if msg_to_edit:
        try:
            await msg_to_edit.edit_text(
                f"✅ <b>{name}</b> (<code>{target_id}</code>) approved!\n"
                f"⏳ Access: <b>{days} days</b> (until {expiry_str})",
                parse_mode="HTML",
            )
        except Exception:
            pass
    try:
        keyboard = await get_main_keyboard(int(target_id))
        await bot.send_message(
            chat_id=int(target_id),
            text=(
                "🎉 <b>Your access has been approved!</b>\n\n"
                f"⏳ <b>Access Duration:</b> {days} days\n"
                f"📅 <b>Valid Until:</b> {expiry_str}\n\n"
                "All features are now unlocked."
            ),
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.error(f"Could not notify user {target_id}: {e}")

async def handle_approval_days_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    text = update.message.text.strip()
    try:
        await update.message.delete()
    except Exception:
        pass
    try:
        days = int(text)
        if days < 1 or days > 3650:
            raise ValueError
    except ValueError:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Enter a valid number of days (1–3650):")
        return WAIT_APPROVAL_DAYS

    target_id = context.user_data.get("approve_target_id")
    msg_id    = context.user_data.get("approve_msg_id")
    if not target_id:
        return ConversationHandler.END

    from datetime import datetime, timezone, timedelta
    udata = await load_user(target_id)
    name  = udata.get("name", "Unknown")
    udata["status"] = "approved"
    expiry_ts  = (datetime.now(timezone.utc) + timedelta(days=days)).timestamp()
    udata["access_expiry"] = expiry_ts
    expiry_str = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")
    await save_user(target_id, udata)

    result_text = (
        f"✅ <b>{name}</b> (<code>{target_id}</code>) approved!\n"
        f"⏳ Access: <b>{days} days</b> (until {expiry_str})"
    )
    if msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id, message_id=msg_id,
                text=result_text, parse_mode="HTML",
            )
        except Exception:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=result_text, parse_mode="HTML")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=result_text, parse_mode="HTML")

    try:
        keyboard = await get_main_keyboard(int(target_id))
        await context.bot.send_message(
            chat_id=int(target_id),
            text=(
                "🎉 <b>Your access has been approved!</b>\n\n"
                f"⏳ <b>Access Duration:</b> {days} days\n"
                f"📅 <b>Valid Until:</b> {expiry_str}\n\n"
                "All features are now unlocked."
            ),
            parse_mode="HTML", reply_markup=keyboard,
        )
    except Exception as e:
        logger.error(f"Could not notify user {target_id}: {e}")

    context.user_data.pop("approve_target_id", None)
    context.user_data.pop("approve_msg_id", None)
    return ConversationHandler.END

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    all_ids  = await list_all_users()
    total    = len(all_ids)
    approved = pending = denied = expired_count = 0
    for uid in all_ids:
        udata  = await load_user(uid)
        status = udata.get("status", "new")
        if status == "approved":
            is_exp, _, _ = _expiry_info(udata)
            if is_exp:
                udata["status"] = "expired"
                await save_user(uid, udata)
                expired_count += 1
                continue
            approved += 1
        elif status == "expired": expired_count += 1
        elif status == "pending": pending += 1
        elif status == "denied":  denied += 1

    text = (
        "🔒 <b>Admin Panel</b>\n\n"
        f"👥 Total Users: {total}\n"
        f"✅ Approved: {approved}\n"
        f"⏳ Pending: {pending}\n"
        f"❌ Denied: {denied}\n"
        f"⏰ Expired: {expired_count}\n\n"
        "Use the buttons below:"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 User List",         callback_data="admin_userlist_0")],
        [InlineKeyboardButton("⏳ Pending Requests",  callback_data="admin_pending")],
        [InlineKeyboardButton("📢 Broadcast Message", callback_data="admin_broadcast_msg")],
        [InlineKeyboardButton("🖼 Broadcast Photo",   callback_data="admin_broadcast_photo")],
    ])
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")
    return ADMIN_BROADCAST

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    data   = query.data
    caller = update.effective_user.id
    if not is_admin(caller):
        return

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
            if status == "approved":
                is_exp, days_left, _ = _expiry_info(udata)
                emoji = "⏰" if is_exp else ("⚠️" if days_left <= 3 else "✅")
            else:
                emoji = {"pending": "⏳", "denied": "❌", "expired": "⏰"}.get(status, "❓")
            name = udata.get("name", "Unknown")[:15]
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
            reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML",
        )

    elif data.startswith("admin_user_"):
        target = data.replace("admin_user_", "")
        udata  = await load_user(target)
        status = udata.get("status", "unknown")
        name   = udata.get("name", "Unknown")
        uname  = udata.get("username", "N/A")
        expired, days_left, expiry_date = _expiry_info(udata)
        if status == "approved":
            validity = f"✅ Active — {days_left}d left ({expiry_date})" if expiry_date != "Unlimited" else "✅ Unlimited"
        elif status == "expired":
            validity = f"⏰ Expired ({expiry_date})"
        else:
            validity = "—"
        text = (
            f"👤 <b>{name}</b>\n"
            f"🆔 <code>{target}</code>\n"
            f"📛 {uname}\n"
            f"📊 Status: <b>{status}</b>\n"
            f"📅 Validity: {validity}"
        )
        row1 = []
        if status != "approved":
            row1.append(InlineKeyboardButton("✅ Approve", callback_data=f"approve_{target}"))
        if status != "denied":
            row1.append(InlineKeyboardButton("❌ Deny", callback_data=f"deny_{target}"))
        rows = [row1] if row1 else []
        if status in ("approved", "expired"):
            rows.append([InlineKeyboardButton("🔄 Extend Access", callback_data=f"extend_{target}")])
        rows.append([InlineKeyboardButton("🗑 Remove User", callback_data=f"admin_remove_{target}")])
        rows.append([InlineKeyboardButton("🔙 Back", callback_data="admin_userlist_0")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML")

    elif data.startswith("extend_"):
        target = data.replace("extend_", "")
        udata  = await load_user(target)
        name   = udata.get("name", "Unknown")
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("1 Day",   callback_data=f"setdays_{target}_1"),
             InlineKeyboardButton("3 Days",  callback_data=f"setdays_{target}_3"),
             InlineKeyboardButton("7 Days",  callback_data=f"setdays_{target}_7")],
            [InlineKeyboardButton("15 Days", callback_data=f"setdays_{target}_15"),
             InlineKeyboardButton("30 Days", callback_data=f"setdays_{target}_30"),
             InlineKeyboardButton("90 Days", callback_data=f"setdays_{target}_90")],
            [InlineKeyboardButton("✍️ Custom", callback_data=f"setdays_{target}_custom")],
            [InlineKeyboardButton("🔙 Back", callback_data=f"admin_user_{target}")],
        ])
        await query.edit_message_text(
            f"🔄 <b>Extend access for {name}</b>\n\nSelect new access duration:",
            reply_markup=keyboard, parse_mode="HTML",
        )

    elif data.startswith("admin_remove_confirm_"):
        target = data.replace("admin_remove_confirm_", "")
        udata  = await load_user(target)
        name   = udata.get("name", "Unknown")
        path   = _user_file(target)
        try:
            os.remove(path)
        except Exception:
            pass
        await query.edit_message_text(
            f"🗑 <b>{name}</b> (<code>{target}</code>) has been removed.", parse_mode="HTML")
        try:
            await context.bot.send_message(
                chat_id=int(target),
                text=f"⛔ <b>You have been removed from the bot.</b>\n\nContact: {ADMIN_USERNAME}",
                parse_mode="HTML",
                reply_markup=ReplyKeyboardMarkup([["📩 Request Access"]], resize_keyboard=True),
            )
        except Exception:
            pass

    elif data.startswith("admin_remove_"):
        target = data.replace("admin_remove_", "")
        udata  = await load_user(target)
        name   = udata.get("name", "Unknown")
        await query.edit_message_text(
            f"⚠️ <b>Remove {name}?</b>\n\n🆔 <code>{target}</code>\n\nThis will delete all their data.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Yes, Remove", callback_data=f"admin_remove_confirm_{target}"),
                 InlineKeyboardButton("❌ Cancel", callback_data=f"admin_user_{target}")],
            ]),
            parse_mode="HTML",
        )

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
            reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML",
        )

    elif data == "admin_broadcast_msg":
        context.user_data["broadcast_type"] = "text"
        context.user_data["broadcast_count"] = 999999
        await query.edit_message_text(
            "📢 <b>Broadcast Message</b>\n\nType your message below.\nIt will be sent to <b>ALL approved users</b>.\n\n(Type /cancel to abort)",
            parse_mode="HTML",
        )
        return ADMIN_BROADCAST

    elif data == "admin_broadcast_photo":
        context.user_data["broadcast_type"] = "photo"
        context.user_data["broadcast_count"] = 999999
        await query.edit_message_text(
            "🖼 <b>Broadcast Photo</b>\n\nSend a photo (with optional caption).\nIt will be sent to <b>ALL approved users</b>.\n\n(Type /cancel to abort)",
            parse_mode="HTML",
        )
        return ADMIN_BROADCAST_PHOTO

    elif data == "admin_back":
        all_ids  = await list_all_users()
        total    = len(all_ids)
        approved = pending = denied = expired_count = 0
        for uid in all_ids:
            udata  = await load_user(uid)
            status = udata.get("status", "new")
            if status == "approved":  approved += 1
            elif status == "pending": pending += 1
            elif status == "denied":  denied += 1
            elif status == "expired": expired_count += 1
        text = (
            "🔒 <b>Admin Panel</b>\n\n"
            f"👥 Total Users: {total}\n"
            f"✅ Approved: {approved}\n"
            f"⏳ Pending: {pending}\n"
            f"❌ Denied: {denied}\n"
            f"⏰ Expired: {expired_count}\n\n"
            "Use the buttons below:"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 User List",         callback_data="admin_userlist_0")],
            [InlineKeyboardButton("⏳ Pending Requests",  callback_data="admin_pending")],
            [InlineKeyboardButton("📢 Broadcast Message", callback_data="admin_broadcast_msg")],
            [InlineKeyboardButton("🖼 Broadcast Photo",   callback_data="admin_broadcast_photo")],
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")

async def handle_broadcast_photo_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    context.user_data["broadcast_type"] = "photo"
    context.user_data["broadcast_count"] = 999999
    await query.edit_message_text(
        "🖼 <b>Broadcast Photo</b>\n\nSend a photo (with optional caption).\nIt will be sent to <b>ALL approved users</b>.\n\n(Type /cancel to abort)",
        parse_mode="HTML",
    )
    return ADMIN_BROADCAST_PHOTO

async def handle_broadcast_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    if update.message.text and update.message.text.strip().lower() == "/cancel":
        await update.message.reply_text("🚫 Broadcast cancelled.")
        return ConversationHandler.END

    message  = update.message.text
    admin_id = update.effective_user.id
    all_ids  = await list_all_users()
    recipients = []
    for uid in all_ids:
        udata = await load_user(uid)
        if udata.get("status") == "approved" and int(uid) != admin_id:
            recipients.append(int(uid))

    total = len(recipients)
    status_msg = await update.message.reply_text(
        f"📢 <b>Broadcasting to {total} users...</b>\n⏳ Please wait...", parse_mode="HTML")

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
                    f"📢 <b>Broadcasting...</b>\n✅ Sent: {sent} | ❌ Failed: {failed} | 📊 Total: {total}",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    await status_msg.edit_text(
        f"✅ <b>Broadcast Complete!</b>\n\n✅ Sent: <b>{sent}</b>\n❌ Failed: <b>{failed}</b>\n👥 Total: <b>{total}</b>",
        parse_mode="HTML",
    )
    return ConversationHandler.END

async def handle_broadcast_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    if not update.message.photo:
        await update.message.reply_text("❌ Please send a photo. Type /cancel to abort.")
        return ADMIN_BROADCAST_PHOTO

    photo    = update.message.photo[-1].file_id
    caption  = update.message.caption or ""
    admin_id = update.effective_user.id
    all_ids  = await list_all_users()
    recipients = []
    for uid in all_ids:
        udata = await load_user(uid)
        if udata.get("status") == "approved" and int(uid) != admin_id:
            recipients.append(int(uid))

    total = len(recipients)
    status_msg = await update.message.reply_text(
        f"🖼 <b>Broadcasting photo to {total} users...</b>\n⏳ Please wait...", parse_mode="HTML")

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
                    f"🖼 <b>Broadcasting...</b>\n✅ Sent: {sent} | ❌ Failed: {failed} | 📊 Total: {total}",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    await status_msg.edit_text(
        f"✅ <b>Broadcast Complete!</b>\n\n🖼 Photo sent\n✅ Sent: <b>{sent}</b>\n❌ Failed: <b>{failed}</b>\n👥 Total: <b>{total}</b>",
        parse_mode="HTML",
    )
    return ConversationHandler.END

async def link_boost_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_approved(update):
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["boost_type"] = "link"
    uid   = str(update.effective_user.id)
    udata = await load_user(uid)
    context.user_data["settings"] = udata.get("settings") or get_default_settings()
    keyboard = await get_old_keyboard(uid, "cookie", "🍪 Use Old Cookies", "old_cookie")
    msg = await update.message.reply_text("🍪 <b>ENTER COOKIES</b>", reply_markup=keyboard, parse_mode="HTML")
    context.user_data["boost_msg_id"] = msg.message_id
    return GET_COOKIES

async def massage_boost_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_approved(update):
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["boost_type"] = "massage"
    uid   = str(update.effective_user.id)
    udata = await load_user(uid)
    context.user_data["settings"] = udata.get("settings") or get_default_settings()
    keyboard = await get_old_keyboard(uid, "cookie", "🍪 Use Old Cookies", "old_cookie")
    msg = await update.message.reply_text("💬 <b>MESSAGE BOOST STARTED</b>\n\n🍪 <b>ENTER COOKIES</b>", reply_markup=keyboard, parse_mode="HTML")
    context.user_data["boost_msg_id"] = msg.message_id
    return GET_COOKIES

async def add_link_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_approved(update):
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["boost_type"] = "add_link"
    uid   = str(update.effective_user.id)
    udata = await load_user(uid)
    context.user_data["settings"] = udata.get("settings") or get_default_settings()
    keyboard = await get_old_keyboard(uid, "cookie", "🍪 Use Old Cookies", "old_cookie")
    msg = await update.message.reply_text("🔗 <b>POST LINK ADD STARTED</b>\n\n🍪 <b>ENTER COOKIES</b>", reply_markup=keyboard, parse_mode="HTML")
    context.user_data["boost_msg_id"] = msg.message_id
    return GET_COOKIES

async def process_cookies_logic(cookies, update, context, chat_id, boost_msg_id, user_id):
    if not boost_msg_id:
        msg = await context.bot.send_message(chat_id=chat_id, text="⏳ <b>Verifying cookies...</b>", parse_mode="HTML")
        context.user_data["boost_msg_id"] = msg.message_id
        boost_msg_id = msg.message_id

    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(executor, partial(_create_session_sync, cookies))

    if isinstance(result, tuple):
        fb_session, msg_err = result
    else:
        fb_session = result
        msg_err = "Invalid cookies!"

    if fb_session is None:
        keyboard = await get_old_keyboard(user_id, "cookie", "🍪 Use Old Cookies", "old_cookie")
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=boost_msg_id,
            text=f"❌ <b>{msg_err}</b>\n\n🍪 <b>ENTER COOKIES</b>",
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
    try:
        await update.message.delete()
    except Exception:
        pass

    # FIX: cookie save করার আগে verify করো, সফল হলেই DB-তে রাখো
    boost_msg_id = context.user_data.get("boost_msg_id")
    if boost_msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id, message_id=boost_msg_id,
                text="⏳ <b>Verifying cookies...</b>", parse_mode="HTML",
            )
        except Exception:
            pass

    result = await asyncio.get_event_loop().run_in_executor(
        executor, partial(_create_session_sync, cookies)
    )
    if isinstance(result, tuple):
        fb_session, msg_err = result
    else:
        fb_session = result
        msg_err = "Invalid cookies!"

    if fb_session is None:
        keyboard = await get_old_keyboard(user_id, "cookie", "🍪 Use Old Cookies", "old_cookie")
        if boost_msg_id:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id, message_id=boost_msg_id,
                text=f"❌ <b>{msg_err}</b>\n\n🍪 <b>ENTER COOKIES</b>",
                reply_markup=keyboard, parse_mode="HTML",
            )
        return GET_COOKIES

    # Cookies valid হলেই DB-তে save করো
    udata = await load_user(user_id)
    udata["cookie"] = cookies
    await save_user(user_id, udata)

    context.user_data["fb_session"] = fb_session
    udata2  = await load_user(user_id)
    buttons = []
    if udata2.get("page_id"):
        buttons.append([InlineKeyboardButton("📄 Use Old Page", callback_data="old_page")])
    buttons.append([InlineKeyboardButton("🔗 Auto URL", callback_data="auto_url")])
    if boost_msg_id:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, message_id=boost_msg_id,
            text="✅ <b>Cookies verified!</b>\n\n📄 <b>ENTER PAGE ID</b>\n<i>(Or click Auto URL to extract from a link)</i>",
            reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML",
        )
    return GET_PAGE_ID

async def old_cookie_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query   = update.callback_query
    await query.answer("Using old cookies!")
    user_id = str(update.effective_user.id)
    udata   = await load_user(user_id)
    cookies = udata.get("cookie")
    if not cookies:
        await query.answer("No saved cookies found!", show_alert=True)
        return GET_COOKIES
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
    query = update.callback_query
    await query.answer()
    boost_msg_id = context.user_data.get("boost_msg_id")
    if boost_msg_id:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, message_id=boost_msg_id,
            text="🔗 <b>PASTE POST LINK</b>\n<i>(Paste the Facebook post link)</i>", parse_mode="HTML",
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
    ad_ids = [x.strip() for x in re.split(r"[,\s]+", raw) if x.strip()]
    ad_ids = list(dict.fromkeys(ad_ids))
    user_id = str(update.effective_user.id)
    context.user_data["ad_account_ids"] = ad_ids
    context.user_data["ad_account_id"]  = ad_ids[0]
    udata = await load_user(user_id)
    udata["ad_account_id"]  = ad_ids[0]
    udata["ad_account_ids"] = ad_ids
    await save_user(user_id, udata)
    return await execute_after_ad_account(
        update, context, update.effective_chat.id, context.user_data.get("boost_msg_id"), user_id,
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
    settings = get_user_settings(context)
    ad_ids   = context.user_data.get("ad_account_ids") or [context.user_data["ad_account_id"]]
    total    = len(ad_ids)

    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=boost_msg_id)
    except Exception:
        pass

    if total == 1:
        status_msg = await context.bot.send_message(chat_id=chat_id, text="🚀 <b>Starting Message Boost...</b>", parse_mode="HTML")
    else:
        pending_lines = "\n".join([f"  ⏳ <code>{aid}</code>" for aid in ad_ids])
        status_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"╔══════════════════════╗\n  🚀 <b>BOOST PENDING</b>\n╚══════════════════════╝\n\n📋 <b>Ad Accounts ({total}):</b>\n{pending_lines}",
            parse_mode="HTML",
        )

    loop    = asyncio.get_event_loop()
    results = []
    for i, ad_acc in enumerate(ad_ids, 1):
        result = await loop.run_in_executor(
            executor,
            partial(
                fb_session.run_massage_boost,
                ad_acc=ad_acc, page=context.user_data["page_id"],
                target=context.user_data["post_id"],
                budget=settings["budget"], duration=settings["duration"],
                currency=settings["currency"], country=settings["country"],
                auto_reply=settings.get("auto_reply") or None,
            ),
        )
        results.append((ad_acc, result))
        if total > 1:
            lines = []
            for aid, res in results:
                if res["success"]:
                    lines.append(f"  ✅ <code>{aid}</code> → <code>{res['campaign_id']}</code>")
                else:
                    lines.append(f"  ❌ <code>{aid}</code> → {res.get('message','Failed')[:40]}")
            for aid in ad_ids[i:]:
                lines.append(f"  ⏳ <code>{aid}</code>")
            done   = sum(1 for _, r in results if r["success"])
            failed = len(results) - done
            try:
                await status_msg.edit_text(
                    f"╔══════════════════════╗\n  🚀 <b>BOOST IN PROGRESS</b>\n╚══════════════════════╝\n\n"
                    f"📣 <b>Message Boost</b> ({i}/{total})\n━━━━━━━━━━━━━━━━━━━━━━\n"
                    + "\n".join(lines) +
                    f"\n━━━━━━━━━━━━━━━━━━━━━━\n✅ Done: {done}  ❌ Failed: {failed}",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    try:
        await status_msg.delete()
    except Exception:
        pass

    done   = sum(1 for _, r in results if r["success"])
    failed = sum(1 for _, r in results if not r["success"])

    if total == 1:
        ad_acc, result = results[0]
        if result["success"]:
            msg_text = (
                "╔══════════════════════╗\n  ✅  <b>BOOST ACTIVATED</b>  ✅\n╚══════════════════════╝\n\n"
                f"📣 <b>Message Boost</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 <b>Campaign ID</b>\n   <code>{result['campaign_id']}</code>\n"
                f"💳 <b>Ad Account</b>\n   <code>{ad_acc}</code>\n━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🌍 <b>Country  :</b>  <code>{settings['country']}</code>\n"
                f"💱 <b>Currency :</b>  <code>{settings['currency']}</code>\n"
                f"💰 <b>Budget   :</b>  <code>{settings['budget']}</code>\n"
                f"⏳ <b>Duration :</b>  <code>{settings['duration']} days</code>\n━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🚀 <i>Your ad is now live!</i>"
            )
        else:
            msg_text = (
                "╔══════════════════════╗\n  ❌  <b>BOOST FAILED</b>  ❌\n╚══════════════════════╝\n\n"
                f"💳 <b>Ad Account:</b> <code>{ad_acc}</code>\n"
                f"🛑 <b>Reason:</b>\n{result.get('message','Unknown error')}"
            )
        await context.bot.send_message(chat_id=chat_id, text=msg_text, parse_mode="HTML")
    else:
        summary = "╔══════════════════════╗\n  ✅ <b>BOOST COMPLETE</b>  ✅\n╚══════════════════════╝\n\n📣 <b>Message Boost Results</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
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
            f"━━━━━━━━━━━━━━━━━━━━━━\n✅ Success: <b>{done}</b>  ❌ Failed: <b>{failed}</b>\n🚀 <i>Your ads are now live!</i>"
        )
        await context.bot.send_message(chat_id=chat_id, text=summary, parse_mode="HTML")

    context.user_data.pop("fb_session", None)
    return ConversationHandler.END

async def process_url_logic(url, update, context, chat_id, boost_msg_id):
    context.user_data["url"] = url
    boost_type = context.user_data.get("boost_type", "link")
    fb_session: FacebookSession = context.user_data["fb_session"]
    settings = get_user_settings(context)
    loop     = asyncio.get_event_loop()
    ad_ids   = context.user_data.get("ad_account_ids") or [context.user_data["ad_account_id"]]
    total    = len(ad_ids)

    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=boost_msg_id)
    except Exception:
        pass

    if boost_type == "add_link":
        status_msg = await context.bot.send_message(chat_id=chat_id, text="⏳ <b>Adding link...</b>", parse_mode="HTML")
        result = await loop.run_in_executor(
            executor,
            partial(fb_session.add_post_link,
                ad_acc=ad_ids[0], page=context.user_data["page_id"],
                target=context.user_data["post_id"], website=url,
            ),
        )
        if result["success"]:
            text = (
                "╔══════════════════════╗\n  ✅  <b>LINK ADDED</b>  ✅\n╚══════════════════════╝\n\n"
                f"🔗 <b>Post Link Added Successfully!</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💳 <b>Ad Account:</b>  <code>{ad_ids[0]}</code>\n"
                f"📄 <b>Page ID    :</b>  <code>{context.user_data['page_id']}</code>\n"
                f"📝 <b>Post ID    :</b>  <code>{context.user_data['post_id']}</code>"
            )
        else:
            text = f"╔══════════════════════╗\n  ❌  <b>FAILED</b>  ❌\n╚══════════════════════╝\n\n🛑 {result.get('message','')}"
        try:
            await status_msg.delete()
        except Exception:
            pass
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        context.user_data.pop("fb_session", None)
        return ConversationHandler.END

    if total == 1:
        status_msg = await context.bot.send_message(chat_id=chat_id, text="🚀 <b>Starting Link Boost...</b>", parse_mode="HTML")
    else:
        pending_lines = "\n".join([f"  ⏳ <code>{aid}</code>" for aid in ad_ids])
        status_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"╔══════════════════════╗\n  🚀 <b>BOOST PENDING</b>\n╚══════════════════════╝\n\n📋 <b>Ad Accounts ({total}):</b>\n{pending_lines}",
            parse_mode="HTML",
        )

    results = []
    for i, ad_acc in enumerate(ad_ids, 1):
        result = await loop.run_in_executor(
            executor,
            partial(fb_session.run_boost,
                ad_acc=ad_acc, page=context.user_data["page_id"],
                target=context.user_data["post_id"], website=url,
                budget=settings["budget"], duration=settings["duration"],
                currency=settings["currency"], country=settings["country"],
            ),
        )
        results.append((ad_acc, result))
        if total > 1:
            lines = []
            for aid, res in results:
                if res["success"]:
                    lines.append(f"  ✅ <code>{aid}</code> → <code>{res['campaign_id']}</code>")
                else:
                    lines.append(f"  ❌ <code>{aid}</code> → {res.get('message','Failed')[:40]}")
            for aid in ad_ids[i:]:
                lines.append(f"  ⏳ <code>{aid}</code>")
            done   = sum(1 for _, r in results if r["success"])
            failed = len(results) - done
            try:
                await status_msg.edit_text(
                    f"╔══════════════════════╗\n  🚀 <b>BOOST IN PROGRESS</b>\n╚══════════════════════╝\n\n"
                    f"🔗 <b>Link Boost</b> ({i}/{total})\n━━━━━━━━━━━━━━━━━━━━━━\n"
                    + "\n".join(lines) +
                    f"\n━━━━━━━━━━━━━━━━━━━━━━\n✅ Done: {done}  ❌ Failed: {failed}",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    try:
        await status_msg.delete()
    except Exception:
        pass

    done   = sum(1 for _, r in results if r["success"])
    failed = sum(1 for _, r in results if not r["success"])

    if total == 1:
        ad_acc, result = results[0]
        if result["success"]:
            msg_text = (
                "╔══════════════════════╗\n  ✅  <b>BOOST ACTIVATED</b>  ✅\n╚══════════════════════╝\n\n"
                f"🔗 <b>Link Boost</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 <b>Campaign ID</b>\n   <code>{result['campaign_id']}</code>\n"
                f"💳 <b>Ad Account</b>\n   <code>{ad_acc}</code>\n━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🌍 <b>Country  :</b>  <code>{settings['country']}</code>\n"
                f"💱 <b>Currency :</b>  <code>{settings['currency']}</code>\n"
                f"💰 <b>Budget   :</b>  <code>{settings['budget']}</code>\n"
                f"⏳ <b>Duration :</b>  <code>{settings['duration']} days</code>\n━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🚀 <i>Your ad is now live!</i>"
            )
        else:
            msg_text = (
                "╔══════════════════════╗\n  ❌  <b>BOOST FAILED</b>  ❌\n╚══════════════════════╝\n\n"
                f"💳 <b>Ad Account:</b> <code>{ad_acc}</code>\n"
                f"🛑 <b>Reason:</b>\n{result.get('message','Unknown error')}"
            )
        await context.bot.send_message(chat_id=chat_id, text=msg_text, parse_mode="HTML")
    else:
        summary = "╔══════════════════════╗\n  ✅ <b>BOOST COMPLETE</b>  ✅\n╚══════════════════════╝\n\n🔗 <b>Link Boost Results</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
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
            f"━━━━━━━━━━━━━━━━━━━━━━\n✅ Success: <b>{done}</b>  ❌ Failed: <b>{failed}</b>\n🚀 <i>Your ads are now live!</i>"
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
    user_id = str(update.effective_user.id)
    udata   = await load_user(user_id)
    db_settings = udata.get("settings", {})
    if db_settings:
        context.user_data["settings"] = db_settings
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
        preview = auto_reply[:60] + "..." if len(auto_reply) > 60 else auto_reply
        ar_text = f"\n💬 <b>Auto Reply:</b> <code>{preview}</code>"
    else:
        ar_text = "\n💬 <b>Auto Reply:</b> <i>Not set</i>"
    return (
        "⚙️ <b>Current Settings</b>\n\n"
        f"🌍 <b>Country:</b> <code>{settings['country']}</code>\n"
        f"💱 <b>Currency:</b> <code>{settings['currency']}</code>\n"
        f"💰 <b>Budget:</b> <code>{settings['budget']}</code>\n"
        f"⏳ <b>Duration:</b> <code>{settings['duration']}</code> days"
        f"{ar_text}\n\nClick a button below to change a setting."
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
    countries = [
        ("🇧🇩 Bangladesh", "BD"), ("🇮🇳 India", "IN"),
        ("🇳🇵 Nepal", "NP"), ("🇹🇭 Thailand", "TH"),
        ("🇰🇪 Kenya", "KE"), ("🇿🇦 South Africa", "ZA"),
        ("🇲🇾 Malaysia", "MY"), ("🇸🇬 Singapore", "SG"),
        ("🇦🇪 UAE", "AE"), ("🇸🇦 Saudi Arabia", "SA"),
        ("🇺🇸 USA", "US"), ("🇬🇧 UK", "GB"),
        ("🇵🇰 Pakistan", "PK"), ("🇪🇬 Egypt", "EG"),
        ("🇮🇩 Indonesia", "ID"), ("🇵🇭 Philippines", "PH"),
    ]
    keyboard = [
        [InlineKeyboardButton(countries[i][0], callback_data=f"save_country_{countries[i][1]}"),
         InlineKeyboardButton(countries[i+1][0], callback_data=f"save_country_{countries[i+1][1]}")]
        for i in range(0, len(countries), 2)
    ]
    keyboard.append([
        InlineKeyboardButton("🌍 Africa Region", callback_data="save_country_AFRICA"),
        InlineKeyboardButton("🌏 Asia Region",   callback_data="save_country_ASIA"),
    ])
    keyboard.append([
        InlineKeyboardButton("🌎 LatAm Region",  callback_data="save_country_LATAM"),
        InlineKeyboardButton("🕌 MENA Region",   callback_data="save_country_MENA"),
    ])
    keyboard.append([
        InlineKeyboardButton("🌐 Europe Region", callback_data="save_country_EUR_REG"),
        InlineKeyboardButton("🌏 APAC Region",   callback_data="save_country_APAC"),
    ])
    keyboard.append([InlineKeyboardButton("✍️ Custom Country/Region", callback_data="custom_country")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_settings")])
    await query.edit_message_text(
        "🌍 <b>Select Country or Region:</b>",
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
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_settings")])
    await query.edit_message_text(
        "💱 <b>Select Currency:</b>",
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
            [InlineKeyboardButton("$1", callback_data="save_budget_1"),
             InlineKeyboardButton("$2", callback_data="save_budget_2"),
             InlineKeyboardButton("$5", callback_data="save_budget_5")],
            [InlineKeyboardButton("$10", callback_data="save_budget_10"),
             InlineKeyboardButton("$15", callback_data="save_budget_15"),
             InlineKeyboardButton("$20", callback_data="save_budget_20")],
            [InlineKeyboardButton("✍️ Custom (1–1000)", callback_data="custom_budget")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_settings")],
        ])
        await query.edit_message_text("💰 <b>Select Daily Budget:</b>", reply_markup=keyboard, parse_mode="HTML")
        return SETTINGS_MENU

    if query.data == "set_duration":
        context.user_data["expected_input"] = "duration"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("1 day",  callback_data="save_duration_1"),
             InlineKeyboardButton("2 days", callback_data="save_duration_2"),
             InlineKeyboardButton("3 days", callback_data="save_duration_3")],
            [InlineKeyboardButton("5 days", callback_data="save_duration_5"),
             InlineKeyboardButton("7 days", callback_data="save_duration_7"),
             InlineKeyboardButton("14 days",callback_data="save_duration_14")],
            [InlineKeyboardButton("✍️ Custom", callback_data="custom_duration")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_settings")],
        ])
        await query.edit_message_text("⏳ <b>Select Duration:</b>", reply_markup=keyboard, parse_mode="HTML")
        return SETTINGS_MENU

    prompts = {
        "custom_country":  "🌍 <b>Enter Country Name or Code:</b>\n\n<i>Name: Bangladesh, India, Saudi Arabia\nCode: BD, IN, SA, US, GB</i>",
        "custom_currency": "💱 <b>Enter Currency Code:</b>\n\n<i>Examples: USD, BDT, SAR, AED, EUR</i>",
        "custom_budget":   "💰 <b>Enter Daily Budget:</b>\n\n<i>Enter a number between 1 and 10000</i>",
        "custom_duration": "⏳ <b>Enter Duration in Days:</b>\n\n<i>Enter a number between 1 and 365</i>",
    }
    keys = {
        "custom_country": "country", "custom_currency": "currency",
        "custom_budget": "budget", "custom_duration": "duration",
    }
    if query.data not in keys:
        return SETTINGS_MENU
    context.user_data["expected_input"] = keys[query.data]
    await query.edit_message_text(prompts[query.data], parse_mode="HTML")
    return WAIT_SETTING_INPUT

async def handle_setting_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    expected  = context.user_data.get("expected_input")
    user_text = update.message.text.strip()
    chat_id   = update.effective_chat.id
    user_id   = str(update.effective_user.id)
    try:
        await update.message.delete()
    except Exception:
        pass

    settings  = context.user_data.setdefault("settings", get_default_settings())
    error     = None

    if expected == "country":
        lookup = user_text.lower().strip()
        if lookup in COUNTRY_NAME_MAP:
            settings["country"] = COUNTRY_NAME_MAP[lookup]
        elif lookup.upper() in REGION_CODES:
            settings["country"] = lookup.upper()
        elif len(user_text) <= 10 and user_text.replace(" ", "").isalpha():
            if len(user_text) == 2:
                settings["country"] = user_text.upper()
            else:
                matched = None
                for name, code in COUNTRY_NAME_MAP.items():
                    if lookup in name or name in lookup:
                        matched = code
                        break
                settings["country"] = matched if matched else user_text.upper()[:6]
        else:
            error = "❌ <b>Invalid country.</b> Enter a country name (e.g., Bangladesh) or 2-letter code (e.g., BD)."

    elif expected == "currency":
        val = user_text.upper().strip().replace(" ", "")
        if 2 <= len(val) <= 5 and val.isalpha():
            settings["currency"] = val[:3] if len(val) > 3 else val
        else:
            error = "❌ <b>Invalid currency.</b> Enter a 3-letter code (e.g., USD, BDT, SAR)."

    elif expected == "budget":
        try:
            val = float(user_text.replace(",", "").replace("$", ""))
            if 1 <= val <= 10000:
                settings["budget"] = val
            else:
                error = "❌ <b>Budget must be between 1 and 10000.</b>"
        except ValueError:
            error = "❌ <b>Invalid budget.</b> Enter a number (e.g., 10 or 5.5)."

    elif expected == "duration":
        try:
            val = int(user_text.strip())
            if 1 <= val <= 365:
                settings["duration"] = val
            else:
                error = "❌ <b>Duration must be between 1 and 365 days.</b>"
        except ValueError:
            error = "❌ <b>Invalid duration.</b> Enter a number (e.g., 7)."

    prompt_msg_id = context.user_data.get("prompt_msg_id")

    if error:
        try:
            if prompt_msg_id:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=prompt_msg_id,
                    text=error + "\n\n<i>Please type again:</i>", parse_mode="HTML",
                )
            else:
                await context.bot.send_message(chat_id=chat_id, text=error, parse_mode="HTML")
        except Exception:
            await context.bot.send_message(chat_id=chat_id, text=error, parse_mode="HTML")
        return WAIT_SETTING_INPUT

    udata = await load_user(user_id)
    udata["settings"] = settings
    await save_user(user_id, udata)
    context.user_data["settings"] = settings

    text     = _get_settings_text(settings)
    keyboard = InlineKeyboardMarkup(_get_settings_keyboard())
    try:
        if prompt_msg_id:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=prompt_msg_id,
                text=text, reply_markup=keyboard, parse_mode="HTML",
            )
        else:
            await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")
    except Exception:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")
    return SETTINGS_MENU

async def show_auto_reply_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()
    settings   = context.user_data.get("settings", {})
    auto_reply = settings.get("auto_reply", "")
    chat_id    = update.effective_chat.id
    msg_id     = query.message.message_id if query else None

    if auto_reply:
        short_preview = auto_reply[:300] + "..." if len(auto_reply) > 300 else auto_reply
        body = f"💬 <b>AUTO REPLY Message</b>\n\n<code>{short_preview}</code>\n\nThis message will be used as greeting in Message Boost."
    else:
        body = "💬 <b>AUTO REPLY Message</b>\n\n<i>No auto reply set yet.</i>\n\nTap below to add your auto reply message."

    buttons = []
    if auto_reply:
        buttons.append([InlineKeyboardButton("✏️ Change Auto Reply", callback_data="ar_add")])
        buttons.append([InlineKeyboardButton("🗑 Remove Auto Reply",  callback_data="ar_clear")])
    else:
        buttons.append([InlineKeyboardButton("➕ Add Auto Reply", callback_data="ar_add")])
    buttons.append([InlineKeyboardButton("🔙 Back to Settings", callback_data="back_to_settings")])

    if query and msg_id:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=msg_id,
            text=body, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML",
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id, text=body,
            reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML",
        )
    return SETTINGS_MENU

async def auto_reply_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id  = str(update.effective_user.id)
    settings = context.user_data.setdefault("settings", {})
    if "auto_reply" not in settings:
        settings["auto_reply"] = ""

    if query.data == "ar_add":
        await query.edit_message_text(
            "💬 <b>Set Auto Reply Message</b>\n\nType your message and send.\n<i>Multi-line messages are supported.</i>",
            parse_mode="HTML",
        )
        return WAIT_AUTO_REPLY
    elif query.data == "ar_clear":
        settings["auto_reply"] = ""
        udata = await load_user(user_id)
        udata["settings"] = settings
        await save_user(user_id, udata)
        context.user_data["settings"] = settings
        return await show_auto_reply_menu(update, context)
    return SETTINGS_MENU

async def handle_auto_reply_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_text = update.message.text.strip()
    if not user_text:
        await update.message.reply_text("❌ <b>Empty message.</b> Please type your auto reply message:", parse_mode="HTML")
        return WAIT_AUTO_REPLY

    user_id  = str(update.effective_user.id)
    settings = context.user_data.setdefault("settings", {})
    settings["auto_reply"] = user_text

    udata = await load_user(user_id)
    udata["settings"] = settings
    await save_user(user_id, udata)
    context.user_data["settings"] = settings

    short_preview = user_text[:200] + "..." if len(user_text) > 200 else user_text
    body = f"✅ <b>Auto Reply Saved!</b>\n\n<code>{short_preview}</code>"
    buttons = [
        [InlineKeyboardButton("✏️ Change Auto Reply", callback_data="ar_add")],
        [InlineKeyboardButton("🗑 Remove Auto Reply",  callback_data="ar_clear")],
        [InlineKeyboardButton("🔙 Back to Settings",   callback_data="back_to_settings")],
    ]
    await update.message.reply_text(text=body, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
    return SETTINGS_MENU

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("🚫 <b>Operation cancelled.</b>", parse_mode="HTML")
    else:
        await update.message.reply_text("🚫 <b>Operation cancelled.</b>", parse_mode="HTML")
    context.user_data.pop("fb_session", None)
    return ConversationHandler.END


# ── Flask app for Render Web Service ─────────────────────
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "Bot is running! ✅"

@flask_app.route("/health")
def health():
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port, use_reloader=False)


def main() -> None:
    # FIX: asyncio event loop সঠিকভাবে set করা
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

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
            r"|🔒 Admin Panel|📩 Request Access|📩 Request Renewal"
            r"|⏳ Waiting for Approval\.\.\.|📩 Request Sent\.\.\.)$"
        )
    )

    admin_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🔒 Admin Panel$"), admin_panel)],
        states={
            ADMIN_BROADCAST: [
                CallbackQueryHandler(handle_broadcast_photo_trigger, pattern="^admin_broadcast_photo$"),
                CallbackQueryHandler(setdays_callback,  pattern="^setdays_"),
                CallbackQueryHandler(approval_callback, pattern="^(approve|deny)_"),
                CallbackQueryHandler(admin_callback,    pattern="^(admin_|extend_|admin_remove_confirm_)"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast_text),
            ],
            ADMIN_BROADCAST_PHOTO: [
                CallbackQueryHandler(admin_callback, pattern="^admin_"),
                MessageHandler(filters.PHOTO, handle_broadcast_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: u.message.reply_text("❌ Please send a photo. /cancel to abort.")),
            ],
            WAIT_APPROVAL_DAYS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_approval_days_input),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
        conversation_timeout=300,
    )

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
    application.add_handler(MessageHandler(
        filters.Regex("^📩 (Request Access|Request Renewal)"), start,
    ))
    application.add_handler(admin_conv)
    application.add_handler(boost_conv)
    application.add_handler(settings_conv)
    application.add_handler(CallbackQueryHandler(approval_callback, pattern="^(approve|deny)_"))
    application.add_handler(CallbackQueryHandler(setdays_callback,  pattern="^setdays_"))
    application.add_handler(CallbackQueryHandler(admin_callback,    pattern="^(admin_|extend_|admin_remove_confirm_)"))

    logger.info("Bot started")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        timeout=30,
    )

if __name__ == "__main__":
    # Flask আলাদা thread-এ চালাও, তারপর bot চালাও
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask server started in background")
    main()
