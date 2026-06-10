import os
import datetime
from fastapi import FastAPI, Query
from fastapi.responses import PlainTextResponse, JSONResponse
import israel_bus_cli
import re

def clean_text(text: str) -> str:
    """ניקוי תווים לא חוקיים של ימות המשיח כדי למנוע קריסה של השרת הטלפוני"""
    if not text:
        return ""
    # הסרה מוחלטת של: נקודה, מקף, גרש, גרשיים, אמפרסנד, פסיק וקו אנכי
    # כולל סימני כיווניות של יוניקוד (LTR/RTL) שעלולים להשתרבב בטקסט
    text = re.sub(r'[.\-\"\'&|,\u200e\u200f]', " ", text)
    # צמצום רווחים כפולים
    text = re.sub(r'\s+', " ", text)
    return text.strip()

def make_ivr_response(text: str, var_name: str, min_digits: int = 1, max_digits: int = 1, sec_wait: int = 7) -> str:
    """ייצור תגובת IVR עם פקודת read ישירה"""
    cleaned_text = clean_text(text)
    return f"read=t-{cleaned_text}={var_name},yes,{max_digits},{min_digits},{sec_wait},No,no,no"

app = FastAPI(title="Yemot Hamashiach Transit IVR (Free Version)")

# קווים ישירים מצומת בית דגן המגיעים סמוך מאוד לשדרות רוטשילד / נחלת בנימין
APPROVED_LINES = {"74", "174", "201", "274", "156"}

# קווים כלליים לתל אביב לצורך סינון בחיפוש מתחנות אחרות
COMMON_TA_LINES = {
    "1", "2", "25", "74", "125", "129", "142", "156", "164", "172", "174", 
    "189", "190", "193", "201", "274", "411", "461"
}

def get_israel_time() -> datetime.datetime:
    """קבלת הזמן הנוכחי לפי שעון ישראל (התמודדות עם שרתי ענן)"""
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

@app.get("/")
@app.get("/transit-route")
def handle_ivr_request(
    select: str = Query(None),       # הוחלף מ-selection כדי למנוע התנגשויות
    saved_route: str = Query(None),  # משתנה שישמר בימות המשיח
    custom_stop: str = Query(None),  # חיפוש לפי מזהה תחנה
    custom_stop_selection: str = Query(None),
    search_stop: str = Query(None),  # פרמטרים שמורים לצורך תהליך החיפוש
    search_lines: str = Query(None),
    ApiCallId: str = Query(None)
):
    # ==========================================
    # 1. תרחיש חיפוש לפי מזהה תחנה מותאם אישית
    # ==========================================
    if select == "4":  # שונה מ-* למקש 4
        # בקשה מהמשתמש להזין מזהה תחנה
        return PlainTextResponse(
            make_ivr_response("נא הקש את מזהה התחנה בן חמש הספרות", "custom_stop", min_digits=5, max_digits=5, sec_wait=10)
        )

    if custom_stop:
        try:
            data = israel_bus_cli.get_lines_by_stop(custom_stop)
        except Exception:
            return PlainTextResponse(
                make_ivr_response("אירעה שגיאה בחיפוש התחנה אנא נסה שוב לחזרה לתפריט הקש 9", "select")
            )
            
        if not data:
            return PlainTextResponse(
                make_ivr_response("לא נמצאו קווים פעילים בתחנה זו לחזרה לתפריט הקש 9", "select")
            )
            
        # סינון קווים שמגיעים לתל אביב
        ta_arrivals = []
        for item in data:
            shilut = str(item.get('Shilut', ''))
            desc = item.get('Description', '') or ''
            if shilut in COMMON_TA_LINES or any(k in desc for k in ["תל אביב", "אלנבי", "כרמלית", "סבידור", "מסוף"]):
                ta_arrivals.append(item)
                
        if not ta_arrivals:
            return PlainTextResponse(
                make_ivr_response("לא נמצאו קווים ישירים לתל אביב בתחנה זו לחזרה לתפריט הקש 9", "select")
            )
            
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
        lines_joined = ",".join(lines_list)
        legal_digits = "".join(str(i + 1) for i in range(len(lines_list))) + "9"
        
        # שמירת פרטי החיפוש במשתנים זמניים בימות המשיח לצורך הצעד הבא
        return PlainTextResponse(
            f"api_set_phone_var_search_stop={custom_stop}&"
            f"api_set_phone_var_search_lines={lines_joined}&"
            f"{make_ivr_response('בתחנה זו ' + menu_text + ' לחזרה לתפריט הראשי הקש 9', 'custom_stop_selection')}"
        )

    if custom_stop_selection:
        if custom_stop_selection == "9":
            return PlainTextResponse("routing=./")
            
        try:
            line_idx = int(custom_stop_selection) - 1
            available_lines = search_lines.split(",")
            if 0 <= line_idx < len(available_lines):
                selected_line = available_lines[line_idx]
                
                # תשאול מחדש לקבלת זמן אמת מדויק לקו שנבחר
                data = israel_bus_cli.get_lines_by_stop(search_stop)
                eta = None
                for item in data:
                    if str(item.get('Shilut', '')) == selected_line:
                        eta = item.get('MinutesToArrival', 0)
                        break
                
                eta_text = f"מגיע בעוד {eta} דקות" if eta is not None else "לא נמצאו זמנים קרובים כעת"
                eta_time = f" שעת הגעה לתחנה משוערת היא {get_eta_time_string(eta)}." if eta is not None else ""
                
                # השמעת התוצאה למשתמש
                return PlainTextResponse(
                    make_ivr_response(f"קו {selected_line} מתחנה {search_stop} {eta_text} {eta_time} לנסיעה חדשה הקש 9", "select")
                )
        except Exception:
            return PlainTextResponse("routing=./")

    # ==========================================
    # 2. מסלול קבוע (בית דגן -> רוטשילד)
    # ==========================================
    
    # שליפת נתונים לתחנה 33440
    try:
        data = israel_bus_cli.get_lines_by_stop("33440")
    except Exception as e:
        return PlainTextResponse(make_ivr_response("שגיאה בקבלת נתונים ממשרד התחבורה לחזרה לתפריט הקש 9", "select"))

    if not data:
        return PlainTextResponse(make_ivr_response("לא נמצאו קווים פעילים בתחנה זו כעת לחזרה לתפריט הקש 9", "select"))

    # סינון רק לקווים המאושרים לתל אביב
    my_arrivals = []
    for item in data:
        shilut = str(item.get('Shilut', ''))
        if shilut in APPROVED_LINES:
            my_arrivals.append(item)

    if not my_arrivals:
        return PlainTextResponse(make_ivr_response("סליחה לא נמצאו אוטובוסים קרובים לתל אביב כעת בתחנה לחזרה לתפריט הקש 9", "select"))

    # מיון לפי זמן הגעה קרוב
    my_arrivals = sorted(my_arrivals, key=lambda x: x.get('MinutesToArrival', 999))

    # א. תרחיש שנבחר קו ספציפי (1, 2 או 3)
    if select in {"1", "2", "3"}:
        try:
            idx = int(select) - 1
            if idx < len(my_arrivals):
                selected = my_arrivals[idx]
                line = str(selected.get('Shilut', ''))
                eta = selected.get('MinutesToArrival', 0)
                eta_time = get_eta_time_string(eta)
                
                drop_station, walk_instructions = get_walking_instructions(line)
                
                msg = (
                    f"בחרת בקו {line}. האוטובוס הבא מגיע לתחנה בעוד {eta} דקות, בשעה {eta_time}. "
                    f"עליך לרדת בתחנת {drop_station}. זמן הנסיעה באוטובוס הוא כעשרים וחמש דקות. "
                    f"לשמיעת הוראות הליכה מפורטות אל שדרות רוטשילד פינת נחלת בנימין הקש 8. "
                    f"לחזרה לתפריט הקש 9."
                )
                
                # שמירת המסלול בטלפון של המשתמש + השמעת הפרטים + המתנה למקש 8 או 9
                return PlainTextResponse(
                    f"api_set_phone_var_saved_route={line}&"
                    f"{make_ivr_response(msg, 'select')}"
                )
        except Exception:
            return PlainTextResponse("routing=./")

    # ב. תרחיש של שמיעת הוראות הליכה ברגל (מקש 8)
    if select == "8":
        # כאן אנחנו צריכים לדעת איזה קו נשמר כדי להקריא את הוראות ההליכה הנכונות
        line_to_use = saved_route if saved_route in APPROVED_LINES else "74"
        _, walk_instructions = get_walking_instructions(line_to_use)
        
        return PlainTextResponse(
            make_ivr_response(f"{walk_instructions} לחזרה לתפריט הראשי הקש 9", "select")
        )

    # ג. תרחיש של מסלול שמור (הוקש 0)
    if select == "0" and saved_route:
        # מחפשים מתי מגיע הקו השמור שלו
        saved_arrival = None
        for item in my_arrivals:
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
            return PlainTextResponse(make_ivr_response(msg, "select"))
        else:
            msg = f"הקו השמור שלך הוא קו {saved_route}, אך לא נמצאה נסיעה קרובה שלו. לרשימת המסלולים המלאה הקש 9."
            return PlainTextResponse(make_ivr_response(msg, "select"))

    # ד. תפריט ראשי (כניסה ראשונית או חזרה עם מקש 9)
    menu_parts = []
    
    # אם יש מסלול שמור, נציע אותו קודם
    if saved_route:
        menu_parts.append(f"לשמיעת המסלול השמור שלך, קו {saved_route}, הקש 0.")

    # הוספת 2-3 האפשרויות הקרובות ביותר
    for i, item in enumerate(my_arrivals[:3]):
        shilut = str(item.get('Shilut', ''))
        eta = item.get('MinutesToArrival', 0)
        num_word = ["ראשון", "שני", "שלישי"]
        num_str = num_word[i] if i < len(num_word) else str(i + 1)
        menu_parts.append(f"למסלול {num_str} עם קו {shilut} מגיע בעוד {eta} דקות, הקש {i + 1}.")
        
    menu_parts.append("להזנת מזהה תחנה אחרת הקש 4.") # שונה מכוכבית למקש 4
    
    menu_text = " ".join(menu_parts)
    
    # בניית המקשים המותרים להקשה
    allowed_keys = ["1", "2", "3", "4"] # שונה מ-* למקש 4
    if saved_route:
        allowed_keys.append("0")
    legal_digits = "".join(allowed_keys)
    
    response_text = make_ivr_response(f"שלום {menu_text}", "select")
    return PlainTextResponse(response_text)
