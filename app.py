import os
import re
import datetime
from flask import Flask, request, Response
import israel_bus_cli

app = Flask(__name__)

# קווים ישירים מצומת בית דגן המגיעים סמוך מאוד לשדרות רוטשילד / נחלת בנימין
APPROVED_LINES = {"74", "174", "201", "274", "156"}

# קווים כלליים לתל אביב לצורך סינון בחיפוש מתחנות אחרות
COMMON_TA_LINES = {
    "1", "2", "25", "74", "125", "129", "142", "156", "164", "172", "174", 
    "189", "190", "193", "201", "274", "411", "461"
}

def clean_text(text: str) -> str:
    """ניקוי תווים לא חוקיים של ימות המשיח כדי למנוע קריסה של השרת הטלפוני"""
    if not text:
        return ""
    # החלפת נקודות בפסיק עם רווחים כדי לשמור על הפסקה יפה בהקראה
    text = text.replace(".", " , ")
    # הסרת מקפים, גרשיים ותווים מיוחדים אחרים שאינם חוקיים ב-TTS
    text = re.sub(r'[-\-"\'&|]', "", text)
    # צמצום רווחים כפולים
    text = re.sub(r'\s+', " ", text)
    return text.strip()

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

@app.route("/", methods=["GET"])
@app.route("/transit-route", methods=["GET"])
def handle_ivr_request():
    # קבלת פרמטרים שנשלחים מימות המשיח
    select = request.args.get("select")
    saved_route = request.args.get("saved_route")
    custom_stop = request.args.get("custom_stop")
    custom_stop_selection = request.args.get("custom_stop_selection")
    search_stop = request.args.get("search_stop")
    search_lines = request.args.get("search_lines")
    
    # ==========================================
    # 1. תרחיש חיפוש לפי מזהה תחנה מותאם אישית
    # ==========================================
    if select == "4":
        raw_msg = "נא הקש את מזהה התחנה בן חמש הספרות, ובסיום הקש סולמית"
        response_text = f"read=t-{clean_text(raw_msg)}=custom_stop,no,5,5,#,no"
        return Response(response_text, mimetype="text/plain; charset=utf-8")

    if custom_stop:
        try:
            data = israel_bus_cli.get_lines_by_stop(custom_stop)
        except Exception:
            raw_msg = "אירעה שגיאה בחיפוש התחנה. אנא נסה שוב. לחזרה לתפריט הקש 9."
            response_text = f"read=t-{clean_text(raw_msg)}=select,no,1,1,9,no"
            return Response(response_text, mimetype="text/plain; charset=utf-8")
            
        if not data:
            raw_msg = "לא נמצאו קווים פעילים בתחנה זו. לחזרה לתפריט הקש 9."
            response_text = f"read=t-{clean_text(raw_msg)}=select,no,1,1,9,no"
            return Response(response_text, mimetype="text/plain; charset=utf-8")
            
        # סינון קווים שמגיעים לתל אביב
        ta_arrivals = []
        for item in data:
            shilut = str(item.get('Shilut', ''))
            desc = item.get('Description', '') or ''
            if shilut in COMMON_TA_LINES or any(k in desc for k in ["תל אביב", "אלנבי", "כרמלית", "סבידור", "מסוף"]):
                ta_arrivals.append(item)
                
        if not ta_arrivals:
            raw_msg = "לא נמצאו קווים ישירים לתל אביב בתחנה זו. לחזרה לתפריט הקש 9."
            response_text = f"read=t-{clean_text(raw_msg)}=select,no,1,1,9,no"
            return Response(response_text, mimetype="text/plain; charset=utf-8")
            
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
        
        raw_msg = f"בתחנה זו. {menu_text} לחזרה לתפריט הראשי הקש 9."
        response_text = (
            f"api_set_phone_var_search_stop={custom_stop}&"
            f"api_set_phone_var_search_lines={lines_joined}&"
            f"read=t-{clean_text(raw_msg)}=custom_stop_selection,no,1,1,{legal_digits},no"
        )
        return Response(response_text, mimetype="text/plain; charset=utf-8")

    if custom_stop_selection:
        if custom_stop_selection == "9":
            return Response("routing=./", mimetype="text/plain; charset=utf-8")
            
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
                
                raw_msg = f"קו {selected_line} מתחנה {search_stop} {eta_text}.{eta_time} לנסיעה חדשה הקש 9."
                response_text = f"read=t-{clean_text(raw_msg)}=select,no,1,1,9,no"
                return Response(response_text, mimetype="text/plain; charset=utf-8")
        except Exception:
            return Response("routing=./", mimetype="text/plain; charset=utf-8")

    # ==========================================
    # 2. מסלול קבוע (בית דגן -> רוטשילד)
    # ==========================================
    
    # שליפת נתונים לתחנה 33440
    try:
        data = israel_bus_cli.get_lines_by_stop("33440")
    except Exception as e:
        err_msg = f"שגיאה בקבלת נתונים ממשרד התחבורה. {str(e)}"
        return Response(f"id_list_message=t-{clean_text(err_msg)}", mimetype="text/plain; charset=utf-8")

    if not data:
        err_msg = "לא נמצאו קווים פעילים בתחנה זו כעת."
        return Response(f"id_list_message=t-{clean_text(err_msg)}", mimetype="text/plain; charset=utf-8")

    # סינון רק לקווים המאושרים לתל אביב
    my_arrivals = []
    for item in data:
        shilut = str(item.get('Shilut', ''))
        if shilut in APPROVED_LINES:
            my_arrivals.append(item)

    if not my_arrivals:
        err_msg = "סליחה. לא נמצאו אוטובוסים קרובים לתל אביב כעת בתחנה."
        return Response(f"id_list_message=t-{clean_text(err_msg)}", mimetype="text/plain; charset=utf-8")

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
                
                response_text = (
                    f"api_set_phone_var_saved_route={line}&"
                    f"read=t-{clean_text(msg)}=select,no,1,1,89,no"
                )
                return Response(response_text, mimetype="text/plain; charset=utf-8")
        except Exception:
            return Response("routing=./", mimetype="text/plain; charset=utf-8")

    # ב. תרחיש של שמיעת הוראות הליכה ברגל (מקש 8)
    if select == "8":
        line_to_use = saved_route if saved_route in APPROVED_LINES else "74"
        _, walk_instructions = get_walking_instructions(line_to_use)
        
        response_text = f"read=t-{clean_text(walk_instructions)} לחזרה לתפריט הראשי הקש 9.=select,no,1,1,9,no"
        return Response(response_text, mimetype="text/plain; charset=utf-8")

    # ג. תרחיש של מסלול שמור (הוקש 0)
    if select == "0" and saved_route:
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
            response_text = f"read=t-{clean_text(msg)}=select,no,1,1,9,no"
            return Response(response_text, mimetype="text/plain; charset=utf-8")
        else:
            msg = f"הקו השמור שלך הוא קו {saved_route}, אך לא נמצאה נסיעה קרובה שלו. לרשימת המסלולים המלאה הקש 9."
            response_text = f"read=t-{clean_text(msg)}=select,no,1,1,9,no"
            return Response(response_text, mimetype="text/plain; charset=utf-8")

    # ד. תפריט ראשי
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
    
    allowed_keys = ["1", "2", "3", "4"]
    if saved_route:
        allowed_keys.append("0")
    legal_digits = "".join(allowed_keys)
    
    response_text = f"read=t-{clean_text('שלום. ' + menu_text)}=select,no,1,1,{legal_digits},no"
    return Response(response_text, mimetype="text/plain; charset=utf-8")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860)
