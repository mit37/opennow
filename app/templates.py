from __future__ import annotations

from datetime import date, time

from app.config import get_settings
from app.models import Category, Language, ServiceResult

MAX_RESULTS_BY_LANG: dict[Language, int] = {Language.EN: 3, Language.ES: 2, Language.VI: 2}
SEGMENT_CHAR_BUDGET: dict[Language, int] = {Language.EN: 306, Language.ES: 134, Language.VI: 134}

_QUERY_LABEL_MAX: dict[Language, int] = {Language.EN: 24, Language.ES: 14, Language.VI: 14}
_MIN_NAME_CAP: dict[Language, int] = {Language.EN: 8, Language.ES: 4, Language.VI: 4}
_MIN_ADDR_CAP: dict[Language, int] = {Language.EN: 8, Language.ES: 4, Language.VI: 4}
_MIN_NEXT_NAME_CAP: dict[Language, int] = {Language.EN: 8, Language.ES: 4, Language.VI: 4}
_CAP_STEP = 2

_CATEGORY_LABEL: dict[Language, dict[Category, str]] = {
    Language.EN: {
        Category.FOOD: "Food",
        Category.PANTRY: "Pantry",
        Category.SHOWER: "Shower",
        Category.DROPIN: "Drop-in",
        Category.WIFI: "Wifi",
        Category.CLOTHES: "Clothes",
    },
    Language.ES: {
        Category.FOOD: "Comida",
        Category.PANTRY: "Despensa",
        Category.SHOWER: "Duchas",
        Category.DROPIN: "Diurno",
        Category.WIFI: "Wifi",
        Category.CLOTHES: "Ropa",
    },
    Language.VI: {
        Category.FOOD: "Do an",
        Category.PANTRY: "Thuc pham",
        Category.SHOWER: "Tam",
        Category.DROPIN: "Ban ngay",
        Category.WIFI: "Wifi",
        Category.CLOTHES: "Quan ao",
    },
}

_WEEKDAY_LABEL: dict[Language, tuple[str, ...]] = {
    Language.EN: ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    Language.ES: ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"),
    Language.VI: (
        "Thu Hai",
        "Thu Ba",
        "Thu Tu",
        "Thu Nam",
        "Thu Sau",
        "Thu Bay",
        "Chu Nhat",
    ),
}

_TODAY_LABEL = {Language.EN: "today", Language.ES: "hoy", Language.VI: "hom nay"}
_TOMORROW_LABEL = {Language.EN: "tomorrow", Language.ES: "manana", Language.VI: "ngay mai"}
_CLOSES_WORD = {Language.EN: "closes", Language.ES: "cierra", Language.VI: "dong cua"}
_NEXT_WORD = {Language.EN: "Next", Language.ES: "Proximo", Language.VI: "Tiep"}


def format_12h(t: time) -> str:
    hour = t.hour % 12
    if hour == 0:
        hour = 12
    suffix = "am" if t.hour < 12 else "pm"
    if t.minute == 0:
        return f"{hour}{suffix}"
    return f"{hour}:{t.minute:02d}{suffix}"


def _day_label(d: date, lang: Language, today: date | None = None) -> str:
    today = today or date.today()
    delta = (d - today).days
    if delta == 0:
        return _TODAY_LABEL[lang]
    if delta == 1:
        return _TOMORROW_LABEL[lang]
    return _WEEKDAY_LABEL[lang][d.weekday()]


def _truncate(text: str, max_len: int) -> str:
    text = text.strip()
    if len(text) <= max_len:
        return text
    if max_len <= 2:
        return text[:max_len]
    return text[: max_len - 2].rstrip() + ".."


def _result_line(idx: int, r: ServiceResult, lang: Language, name_cap: int, addr_cap: int) -> str:
    name = _truncate(r.name, name_cap)
    addr = _truncate(r.address, addr_cap)
    dist = f"{r.distance_miles:.1f}"
    closes_time = format_12h(r.closes_at) if r.closes_at is not None else None
    if lang is Language.EN:
        desc = _CATEGORY_LABEL[lang][r.category]
        if closes_time is not None:
            closes_word = _CLOSES_WORD[lang]
            return f"{idx}. {name}, {addr}. {desc}, {closes_word} {closes_time}. {dist} mi"
        return f"{idx}. {name}, {addr}. {desc}. {dist} mi"
    # ES/VI: category and the "closes" word are dropped to fit the halved
    # 2-segment UCS-2 budget (134 chars) for up to 2 results plus header/footer.
    if closes_time is not None:
        return f"{idx}. {name}, {addr}. {closes_time}, {dist}mi"
    return f"{idx}. {name}, {addr}. {dist}mi"


_RESULTS_HEADER = {
    Language.EN: "Open now near {label}:",
    Language.ES: "Abierto cerca de {label}:",
    Language.VI: "Dang mo gan {label}:",
}

_RESULTS_FOOTER = {
    Language.EN: "Reply MORE, SHOWER, or SHELTER.",
    Language.ES: "Responde MORE, SHOWER o SHELTER.",
    Language.VI: "Nhan MORE, SHOWER, hoac SHELTER.",
}


def render_results(results: list[ServiceResult], lang: Language, query_label: str) -> str:
    results = results[: MAX_RESULTS_BY_LANG[lang]]
    label = _truncate(query_label, _QUERY_LABEL_MAX[lang])
    header = _RESULTS_HEADER[lang].format(label=label)
    footer = _RESULTS_FOOTER[lang]
    budget = SEGMENT_CHAR_BUDGET[lang]
    min_name_cap = _MIN_NAME_CAP[lang]
    min_addr_cap = _MIN_ADDR_CAP[lang]

    name_cap, addr_cap = 40, 35
    candidate = header
    while True:
        lines = [_result_line(i + 1, r, lang, name_cap, addr_cap) for i, r in enumerate(results)]
        candidate = "\n".join([header, *lines, footer])
        if len(candidate) <= budget or (name_cap <= min_name_cap and addr_cap <= min_addr_cap):
            return candidate
        name_cap = max(min_name_cap, name_cap - _CAP_STEP)
        addr_cap = max(min_addr_cap, addr_cap - _CAP_STEP)


_NO_RESULTS_HEADER = {
    Language.EN: "Nothing near {label} is open now.",
    Language.ES: "Nada cerca de {label} esta abierto.",
    Language.VI: "Khong co noi gan {label} dang mo.",
}

_NO_RESULTS_CLOSING = {
    Language.EN: "Need shelter tonight? Text SHELTER.",
    Language.ES: "Necesitas refugio? Escribe SHELTER.",
    Language.VI: "Can cho o dem nay? Nhan SHELTER.",
}


def render_no_results(
    next_openings: list[tuple[str, date, time]], lang: Language, query_label: str
) -> str:
    label = _truncate(query_label, _QUERY_LABEL_MAX[lang])
    header = _NO_RESULTS_HEADER[lang].format(label=label)
    closing = _NO_RESULTS_CLOSING[lang]
    next_word = _NEXT_WORD[lang]
    budget = SEGMENT_CHAR_BUDGET[lang]
    min_name_cap = _MIN_NEXT_NAME_CAP[lang]
    openings = next_openings[:2]

    name_cap = 30
    candidate = header
    while True:
        lines = [
            f"{next_word}: {_truncate(name, name_cap)} {format_12h(t)} {_day_label(d, lang)}"
            for name, d, t in openings
        ]
        candidate = "\n".join([header, *lines, closing])
        if len(candidate) <= budget or name_cap <= min_name_cap:
            return candidate
        name_cap = max(min_name_cap, name_cap - _CAP_STEP)


def render_shelter(in_here4you_hours: bool, lang: Language) -> str:
    settings = get_settings()
    here4you = settings.here4you_phone
    if in_here4you_hours:
        return {
            Language.EN: f"Call Here4You at {here4you} for shelter help, 24/7.",
            Language.ES: f"Llama a Here4You al {here4you} para ayuda de refugio, 24/7.",
            Language.VI: f"Goi Here4You so {here4you} de duoc giup cho o, 24/7.",
        }[lang]
    crisis = settings.crisis_line_phone
    return {
        Language.EN: (
            f"Here4You: {here4you} for shelter help. After hours, county crisis "
            f"line: {crisis}. Text your location for the nearest open drop-in."
        ),
        Language.ES: (
            f"Here4You: {here4you} para refugio. Fuera de horario, linea de "
            f"crisis: {crisis}. Envia tu ubicacion para el centro mas cercano."
        ),
        Language.VI: (
            f"Here4You: {here4you} de duoc giup cho o. Ngoai gio, duong day "
            f"khung hoang: {crisis}. Nhan vi tri de tim noi mo som nhat."
        ),
    }[lang]


def render_help(lang: Language) -> str:
    return {
        Language.EN: (
            "Text a ZIP code or two cross streets to find open help near you. "
            "Text SHELTER, ALERTS, or STOP anytime."
        ),
        Language.ES: (
            "Envia tu codigo postal o dos calles que se cruzan para buscar "
            "ayuda. Escribe SHELTER, ALERTS o STOP."
        ),
        Language.VI: (
            "Nhan ma ZIP hoac hai con duong giao nhau de tim noi giup do. "
            "Nhan SHELTER, ALERTS, hoac STOP."
        ),
    }[lang]


def render_stop_ack(lang: Language) -> str:
    return {
        Language.EN: (
            "You're unsubscribed from OpenNow. No more texts will be sent. "
            "Reply START to resubscribe."
        ),
        Language.ES: (
            "Cancelaste tu suscripcion a OpenNow. No mas mensajes. Responde "
            "START para volver a suscribirte."
        ),
        Language.VI: (
            "Ban da huy dang ky OpenNow. Se khong con tin nhan. Nhan START "
            "de dang ky lai."
        ),
    }[lang]


def render_start_ack(lang: Language) -> str:
    return {
        Language.EN: "You're subscribed to OpenNow. Text HELP for help, STOP to stop.",
        Language.ES: "Estas suscrito a OpenNow. Escribe HELP para ayuda, STOP para cancelar.",
        Language.VI: "Ban da dang ky OpenNow. Nhan HELP de duoc giup, STOP de huy.",
    }[lang]


def render_alerts_confirm_prompt(zip_code: str, lang: Language) -> str:
    zip_code = _truncate(zip_code, 10)
    return {
        Language.EN: (
            f"Reply YES to confirm food alerts near {zip_code}. Msg & data "
            "rates may apply. Reply STOP to cancel."
        ),
        Language.ES: (
            f"Responde YES para confirmar alertas de comida cerca de "
            f"{zip_code}. Responde STOP para cancelar."
        ),
        Language.VI: (
            f"Nhan YES de xac nhan canh bao do an gan {zip_code}. Nhan STOP de huy."
        ),
    }[lang]


def render_alerts_confirmed(lang: Language) -> str:
    return {
        Language.EN: "You're confirmed for food alerts near you. Text STOP anytime to stop.",
        Language.ES: "Confirmado. Recibiras alertas de comida cerca de ti. Escribe STOP para cancelar.",
        Language.VI: "Da xac nhan. Ban se nhan canh bao do an gan ban. Nhan STOP de huy.",
    }[lang]


def render_ambiguous_location(lang: Language) -> str:
    return {
        Language.EN: (
            "We couldn't find that place. Try a ZIP code or two cross "
            "streets, like 'King and Story'."
        ),
        Language.ES: (
            "No encontramos ese lugar. Envia un codigo postal o dos calles, "
            "como 'King y Story'."
        ),
        Language.VI: (
            "Khong tim thay noi do. Nhan ma ZIP hoac hai duong giao nhau, "
            "vi du 'King va Story'."
        ),
    }[lang]


def render_crisis_prefix(lang: Language) -> str:
    settings = get_settings()
    crisis = settings.crisis_line_phone
    return {
        Language.EN: f"In crisis? Call or text 988, or call the crisis line at {crisis}.",
        Language.ES: f"En crisis? Llama o envia texto al 988, o llama a la linea de crisis {crisis}.",
        Language.VI: f"Dang khung hoang? Goi hoac nhan 988, hoac goi duong day khung hoang {crisis}.",
    }[lang]


def render_generic_error(lang: Language) -> str:
    return {
        Language.EN: "Sorry, something went wrong. Please text your ZIP code again in a minute.",
        Language.ES: "Algo salio mal. Por favor envia tu codigo postal de nuevo en un minuto.",
        Language.VI: "Xin loi, co loi xay ra. Vui long nhan lai ma ZIP sau mot phut.",
    }[lang]


def render_unknown_zip(lang: Language) -> str:
    return {
        Language.EN: "We don't recognize that ZIP code. Try a 5-digit ZIP or two cross streets.",
        Language.ES: "No reconocemos ese codigo postal. Intenta uno de 5 digitos o dos calles.",
        Language.VI: "Khong nhan ra ma ZIP do. Thu ma ZIP 5 so hoac hai duong giao nhau.",
    }[lang]


def render_out_of_county(lang: Language) -> str:
    return {
        Language.EN: "OpenNow only covers Santa Clara County right now.",
        Language.ES: "Por ahora OpenNow solo cubre el condado de Santa Clara.",
        Language.VI: "Hien OpenNow chi phu vu Quan Santa Clara.",
    }[lang]


def render_provider_closed_ack(lang: Language) -> str:
    return {
        Language.EN: "Got it, marked CLOSED TODAY. Your listing is hidden until tomorrow.",
        Language.ES: "Listo, marcado como CERRADO HOY. Tu lugar estara oculto hasta manana.",
        Language.VI: "Da ghi nhan DONG CUA HOM NAY. Noi cua ban se an den ngay mai.",
    }[lang]


def render_nearest_dropin_line(result: ServiceResult, lang: Language) -> str:
    closes = format_12h(result.closes_at) if result.closes_at is not None else None
    name = _truncate(result.name, 30)
    dist = f"{result.distance_miles:.1f}"
    if lang is Language.EN:
        if closes is not None:
            return f"Nearest open drop-in: {name}, closes {closes}. {dist} mi"
        return f"Nearest open drop-in: {name}. {dist} mi"
    if closes is not None:
        return f"{name}, {closes}. {dist}mi"
    return f"{name}. {dist}mi"


def render_no_more_results(lang: Language) -> str:
    return {
        Language.EN: "That's everything open nearby right now. Text a new ZIP to search again.",
        Language.ES: "Eso es todo lo abierto cerca ahora. Envia otro codigo postal para buscar de nuevo.",
        Language.VI: "Do la tat ca noi dang mo gan day. Nhan ma ZIP khac de tim lai.",
    }[lang]
