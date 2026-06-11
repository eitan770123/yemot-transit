import os
import datetime
import requests
import asyncio
import edge_tts
from fastapi import FastAPI, Query
from fastapi.responses import PlainTextResponse, JSONResponse
import israel_bus_cli
import re

app = FastAPI()

def clean_text(text: str) -> str:
    """ניקוי תווים לא חוקיים של ימות המשיח כדי למנוע קריסה של השרת הטלפוני"""
    if not text:
        return ""
    # הסרה מוחלטת של: נקודה, מקף, גרש, גרשיים, אמפרסנד, פסיק, קו אנכי, נקודתיים ונקודה-פסיק
    # כולל סימני כיווניות של יוניקוד (LTR/RTL) שעלולים להשתרבב בטקסט
    text = re.sub(r'[.\-\"\'\&|,:;\u200e\u200f]', " ", text)
    # צמצום רווחים כפולים
    text = re.sub(r'\s+', " ", text)
    return text.strip()

import json

SAVED_ROUTES_FILE = "saved_routes.json"

def load_saved_routes():
    if os.path.exists(SAVED_ROUTES_FILE):
        try:
            with open(SAVED_ROUTES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_saved_routes(routes):
    try:
        with open(SAVED_ROUTES_FILE, "w", encoding="utf-8") as f:
            json.dump(routes, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

saved_routes = load_saved_routes()
active_sessions = {}

def generate_tts_file(text: str, filename: str):
    import threading
    def _run():
        asyncio.run(edge_tts.Communicate(text, "he-IL-AvriNeural").save(filename))
    thread = threading.Thread(target=_run)
    thread.start()
    thread.join()

def speak_text_external(text: str, phone: str) -> str:
    token = os.getenv("YEMOT_TOKEN")
    if not token:
        return f"t-{text}"
        
    local_filename = f"temp_{phone}.mp3"
    yemot_path = f"ivr2:/2/speech_{phone}.wav"
    url = "https://www.call2all.co.il/ym/api/UploadFile"
    
    try:
        generate_tts_file(text, local_filename)
        data = {
            "token": token,
            "path": yemot_path,
            "convertAudio": "1"
        }
        with open(local_filename, "rb") as f:
            files = {"file": (f"speech_{phone}.mp3", f)}
            response = requests.post(url, data=data, files=files)
            
        if os.path.exists(local_filename):
            os.remove(local_filename)
            
        res_json = response.json()
        if res_json.get("responseStatus") == "OK":
            return f"f-2/speech_{phone}.wav"
    except Exception as e:
        print(f"Error handling external TTS: {e}")
        if os.path.exists(local_filename):
            try:
                os.remove(local_filename)
            except Exception:
                pass
                
    return f"t-{text}"
def make_ivr_response(text: str, phone: str, var_name: str = "select", min_digits: int = 1, max_digits: int = 1, sec_wait: int = 7) -> str:
    """ייצור תגובת IVR עם פקודת read ישירה"""
    cleaned_text = clean_text(text)
    tts_prefix_content = speak_text_external(cleaned_text, phone)
    return f"read={tts_prefix_content}={var_name},yes,{max_digits},{min_digits},{sec_wait},Digits,no"

def get_israel_time() -> datetime.datetime:
    """קבלת הזמן הנוכחי לפי שעון ישראל"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("Asia/Jerusalem"))
    except Exception:
        # חישוב ידני במידה ואין zoneinfo (שעון קיץ בישראל הוא UTC+3)
        return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=3)

def get_eta_time_string(minutes: int) -> str:
    """חישוב שעת הגעה משוערת בפורמט HH:MM"""
    il_time = get_israel_time()
    eta_time = il_time + datetime.timedelta(minutes=minutes)
    return eta_time.strftime("%H:%M")

def get_walking_instructions(line: str) -> tuple[str, str]:
    """החזרת תחנת ירידה והוראות הליכה מותאמות אישית לפי קו האוטובוס"""
    if line in {"74", "174", "274", "156"}:
        station = "יהודה הלוי פינת אלנבי"
        walk = (
            "הוראות הליכה: רד בתחנת יהודה הלוי פינת אלנבי. "
            "לך דרומה ברחוב יהודה הלוי, פנה שמאלה באלנבי, ולאחר מכן פנה ימינה לשדרות רוטשילד. "
            "מפגש הרחובות רוטשילד ונחלת בנימין יהיה מיד מצד ימין שלך."
        )
    elif line == "201":
        station = "דרך מנחם בגין פינת אלנבי"
        walk = (
            "הוראות הליכה: רד בתחנת דרך מנחם בגין פינת אלנבי. "
            "לך מערבה ברחוב אלנבי כ-3 דקות עד לשדרות רוטשילד, ופנה שמאלה. "
            "מפגש הרחובות רוטשילד ונחלת בנימין יהיה מצד ימין שלך."
        )
    else:
        station = "תחנת אלנבי או דרך מנחם בגין"
        walk = (
            "הוראות הליכה: רד בתחנה הקרובה ביותר לרחוב אלנבי. "
            "לך לאורך רחוב אלנבי עד לשדרות רוטשילד, ופנה לכיוון נחלת בנימין."
        )
    return station, walk

def get_main_menu_response(phone: str, saved_route: str, session: dict) -> str:
    try:
        data = israel_bus_cli.get_lines_by_stop("33440")
    except Exception:
        return clean_text("שגיאה בקבלת נתונים ממשרד התחבורה לחזרה לתפריט הקש 9")
        
    if not data:
        return clean_text("לא נמצאו קווים פעילים בתחנה זו כעת לחזרה לתפריט הקש 9")
        
    my_arrivals = []
    for item in data:
        shilut = str(item.get('Shilut', ''))
        if shilut in APPROVED_LINES:
            my_arrivals.append(item)
            
    if not my_arrivals:
        return clean_text("סליחה לא נמצאו אוטובוסים קרובים לתל אביב כעת בתחנה לחזרה לתפריט הקש 9")
        
    my_arrivals = sorted(my_arrivals, key=lambda x: x.get('MinutesToArrival', 999))
    
    # שמירה ב-Session למניעת פניות כפולות
    session["arrivals"] = my_arrivals
    session["arrivals_time"] = datetime.datetime.now()
    
    menu_parts = []
    if saved_route:
        menu_parts.append(f"לשמיעת המסלול השמור שלך, קו {saved_route}, הקש 0.")
        
    for i, item in enumerate(my_arrivals[:3]):
        shilut = str(item.get('Shilut', ''))
        eta = item.get('MinutesToArrival', 0)
        num_word = ["ראשון", "שני", "שלישי"]
        num_str = num_word[i] if i < len(num_word) else str(i + 1)
        menu_parts.append(f"למסלול {num_str} עם קו {shilut} מגיע בעוד {eta} דקות, הקש {i + 1}.")
        
    menu_parts.append("להזנת מזהה תחנה אחרת הקש 4.")
    menu_text = " ".join(menu_parts)
    return clean_text(f"שלום {menu_text}")

@app.get("/")
@app.get("/transit-route")
def handle_ivr_request(
    select: str = Query(None),
    ApiPhone: str = Query(None),
    phone: str = Query(None)
):
    caller_phone = ApiPhone or phone or "default"
    if select:
        select = select.strip()
        
    session = active_sessions.setdefault(caller_phone, {})
    saved_route = saved_routes.get(caller_phone)
    
    # 1. חזרה לתפריט הראשי או כניסה ראשונית ללא בחירה
    if not select or select == "9":
        session.clear()
        menu_msg = get_main_menu_response(caller_phone, saved_route, session)
        return PlainTextResponse(make_ivr_response(menu_msg, caller_phone, "select", 1, 1))
        
    # 2. תפריט שמור - הקשת 0
    if select == "0" and saved_route:
        saved_arrival = None
        arrivals = session.get("arrivals")
        arrivals_time = session.get("arrivals_time")
        
        # שימוש בנתונים השמורים ב-Session אם קיימים וטריים (פחות מ-2 דקות)
        if arrivals and arrivals_time and (datetime.datetime.now() - arrivals_time).total_seconds() < 120:
            for item in arrivals:
                if str(item.get('Shilut', '')) == saved_route:
                    saved_arrival = item
                    break
                    
        if not saved_arrival:
            try:
                data = israel_bus_cli.get_lines_by_stop("33440")
            except Exception:
                return PlainTextResponse(make_ivr_response("שגיאה בקבלת נתונים ממשרד התחבורה לחזרה לתפריט הקש 9", caller_phone, "select", 1, 1))
                
            if not data:
                return PlainTextResponse(make_ivr_response("לא נמצאו קווים פעילים בתחנה זו כעת לחזרה לתפריט הקש 9", caller_phone, "select", 1, 1))
                
            for item in data:
                if str(item.get('Shilut', '')) == saved_route:
                    saved_arrival = item
                    break
                    
        if saved_arrival:
            eta = saved_arrival.get('MinutesToArrival', 0)
            eta_time = get_eta_time_string(eta)
            drop_station, _ = get_walking_instructions(saved_route)
            
            msg = (
                f"המסלול השמור שלך הוא קו {saved_route}. האוטובוס מגיע בעוד {eta} דקות, "
                f"בשעה {eta_time}. לרשימת המסלולים המלאה הקש 9."
            )
            return PlainTextResponse(make_ivr_response(msg, caller_phone, "select", 1, 1))
        else:
            msg = f"הקו השמור שלך הוא קו {saved_route}, אך לא נמצאה נסיעה קרובה שלו. לרשימת המסלולים המלאה הקש 9."
            return PlainTextResponse(make_ivr_response(msg, caller_phone, "select", 1, 1))
            
    # 3. מעבר למסלול מותאם אישית - הקשת 4
    if select == "4":
        session.clear()
        return PlainTextResponse(make_ivr_response("נא הקש את מזהה התחנה בן חמש הספרות", caller_phone, "select", 5, 5, 10))
        
    # 4. הזנת תחנה מותאמת אישית (5 ספרות)
    if len(select) == 5 and select.isdigit():
        custom_stop = select
        try:
            data = israel_bus_cli.get_lines_by_stop(custom_stop)
        except Exception:
            return PlainTextResponse(make_ivr_response("אירעה שגיאה בחיפוש התחנה אנא נסה שוב לחזרה לתפריט הקש 9", caller_phone, "select", 1, 1))
            
        if not data:
            return PlainTextResponse(make_ivr_response("לא נמצאו קווים פעילים בתחנה זו לחזרה לתפריט הקש 9", caller_phone, "select", 1, 1))
            
        # סינון קווים שמגיעים לתל אביב
        ta_arrivals = []
        for item in data:
            shilut = str(item.get('Shilut', ''))
            desc = item.get('Description', '') or ''
            if shilut in COMMON_TA_LINES or any(k in desc for k in ["תל אביב", "אלנבי", "כרמלית", "סבידור", "מסוף"]):
                ta_arrivals.append(item)
                
        if not ta_arrivals:
            return PlainTextResponse(make_ivr_response("לא נמצאו קווים ישירים לתל אביב בתחנה זו לחזרה לתפריט הקש 9", caller_phone, "select", 1, 1))
            
        # בניית תפריט קווים זמינים (עד 3)
        menu_parts = []
        lines_list = []
        for i, item in enumerate(ta_arrivals[:3]):
            shilut = str(item.get('Shilut', ''))
            eta = item.get('MinutesToArrival', 0)
            lines_list.append(shilut)
            
            num_word = ["ראשון", "שני", "שלישי"]
            num_str = num_word[i] if i < len(num_word) else str(i + 1)
            menu_parts.append(f"לקו {shilut} מגיע בעוד {eta} דקות, הקש {i + 1}.")
            
        menu_text = " ".join(menu_parts)
        
        # שמירה ב-Session
        session["search_stop"] = custom_stop
        session["search_lines"] = lines_list
        session["search_arrivals"] = ta_arrivals[:3]
        session["search_arrivals_time"] = datetime.datetime.now()
        
        raw_msg = f"בתחנה זו. {menu_text} לחזרה לתפריט הראשי הקש 9."
        return PlainTextResponse(make_ivr_response(raw_msg, caller_phone, "select", 1, 1))
        
    # 5. השמעת הוראות הליכה - הקשת 8
    if select == "8":
        line_to_use = session.get("selected_route") or saved_route or "74"
        _, walk_instructions = get_walking_instructions(line_to_use)
        return PlainTextResponse(make_ivr_response(f"{walk_instructions} לחזרה לתפריט הראשי הקש 9", caller_phone, "select", 1, 1))
        
    # 6. בחירות קווים (מקשים 1, 2, 3)
    if select in {"1", "2", "3"}:
        search_stop = session.get("search_stop")
        search_lines = session.get("search_lines", [])
        
        # א. אם אנו בתוך תפריט חיפוש תחנה
        if search_stop:
            try:
                line_idx = int(select) - 1
                if 0 <= line_idx < len(search_lines):
                    selected_line = search_lines[line_idx]
                    
                    eta = None
                    search_arrivals = session.get("search_arrivals")
                    search_arrivals_time = session.get("search_arrivals_time")
                    
                    if search_arrivals and search_arrivals_time and (datetime.datetime.now() - search_arrivals_time).total_seconds() < 120:
                        for item in search_arrivals:
                            if str(item.get('Shilut', '')) == selected_line:
                                eta = item.get('MinutesToArrival', 0)
                                break
                                
                    if eta is None:
                        data = israel_bus_cli.get_lines_by_stop(search_stop)
                        for item in data:
                            if str(item.get('Shilut', '')) == selected_line:
                                eta = item.get('MinutesToArrival', 0)
                                break
                            
                    eta_text = f"מגיע בעוד {eta} דקות" if eta is not None else "לא נמצאו זמנים קרובים כעת"
                    eta_time = f" שעת הגעה לתחנה משוערת היא {get_eta_time_string(eta)}." if eta is not None else ""
                    
                    raw_msg = f"קו {selected_line} מתחנה {search_stop} {eta_text} {eta_time} לנסיעה חדשה הקש 9"
                    return PlainTextResponse(make_ivr_response(raw_msg, caller_phone, "select", 1, 1))
            except Exception:
                pass
            session.clear()
            return PlainTextResponse(make_ivr_response(get_main_menu_response(caller_phone, saved_route, session), caller_phone, "select", 1, 1))
            
        # ב. אם אנו בתפריט הראשי (בית דגן)
        else:
            my_arrivals = session.get("arrivals")
            arrivals_time = session.get("arrivals_time")
            
            if not my_arrivals or not arrivals_time or (datetime.datetime.now() - arrivals_time).total_seconds() > 120:
                try:
                    data = israel_bus_cli.get_lines_by_stop("33440")
                except Exception:
                    return PlainTextResponse(make_ivr_response("שגיאה בקבלת נתונים ממשרד התחבורה לחזרה לתפריט הקש 9", caller_phone, "select", 1, 1))
                    
                if not data:
                    return PlainTextResponse(make_ivr_response("לא נמצאו קווים פעילים בתחנה זו כעת לחזרה לתפריט הקש 9", caller_phone, "select", 1, 1))
                    
                my_arrivals = []
                for item in data:
                    shilut = str(item.get('Shilut', ''))
                    if shilut in APPROVED_LINES:
                        my_arrivals.append(item)
                        
                my_arrivals = sorted(my_arrivals, key=lambda x: x.get('MinutesToArrival', 999))
                session["arrivals"] = my_arrivals
                session["arrivals_time"] = datetime.datetime.now()
                
            try:
                idx = int(select) - 1
                if idx < len(my_arrivals):
                    selected = my_arrivals[idx]
                    line = str(selected.get('Shilut', ''))
                    eta = selected.get('MinutesToArrival', 0)
                    eta_time = get_eta_time_string(eta)
                    
                    drop_station, walk_instructions = get_walking_instructions(line)
                    
                    # שמירה לצמיתות
                    saved_routes[caller_phone] = line
                    save_saved_routes(saved_routes)
                    
                    # שמירה זמנית ב-Session
                    session["selected_route"] = line
                    
                    msg = (
                        f"בחרת בקו {line}. האוטובוס הבא מגיע לתחנה בעוד {eta} דקות, בשעה {eta_time}. "
                        f"עליך לרדת בתחנת {drop_station}. זמן הנסיעה באוטובוס הוא כעשרים וחמש דקות. "
                        f"לשמיעת הוראות הליכה מפורטות אל שדרות רוטשילד פינת נחלת בנימין הקש 8. "
                        f"לחזרה לתפריט הקש 9."
                    )
                    return PlainTextResponse(make_ivr_response(msg, caller_phone, "select", 1, 1))
            except Exception:
                pass
            session.clear()
            return PlainTextResponse(make_ivr_response(get_main_menu_response(caller_phone, saved_route, session), caller_phone, "select", 1, 1))
