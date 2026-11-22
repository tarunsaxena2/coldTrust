"""Hindi (Devanagari) + English alert texts."""

PRIORITY = ["limit", "eta", "door", "high", "cold", "drift", "ml"]

HI = {
    "drift": "चैंबर {n} का तापमान धीरे-धीरे बढ़ रहा है ({temp}°C)। कृपया दरवाज़ा और कंप्रेसर जाँच लें।",
    "high": "चेतावनी: चैंबर {n} का तापमान सामान्य से ज़्यादा है ({temp}°C)। तुरंत जाँच करें।",
    "eta": "ख़तरा: चैंबर {n} का तापमान {temp}°C है और लगभग {eta} मिनट में सीमा पार कर सकता है। अभी टेक्नीशियन को बुलाएँ।",
    "limit": "ख़तरा: चैंबर {n} का तापमान सीमा ({limit}°C) पार कर गया है ({temp}°C)। अभी टेक्नीशियन को बुलाएँ।",
    "door": "चैंबर {n} का दरवाज़ा {minutes} मिनट से खुला है। कृपया दरवाज़ा बंद करें।",
    "cold": "चैंबर {n} का तापमान बहुत कम है ({temp}°C)। कृपया जाँच करें।",
    "ml": "चैंबर {n} का तापमान पैटर्न सामान्य से अलग दिख रहा है ({temp}°C)। कृपया जाँच करें।",
}
EN = {
    "drift": "Chamber {n}: temperature is drifting up ({temp} C). Please check the door and compressor.",
    "high": "Warning: Chamber {n} is warmer than normal ({temp} C). Check immediately.",
    "eta": "Critical: Chamber {n} is at {temp} C and may cross its limit in about {eta} min. Call the technician now.",
    "limit": "Critical: Chamber {n} crossed its limit ({limit} C), now {temp} C. Call the technician now.",
    "door": "Chamber {n}: door has been open for {minutes} min. Please close it.",
    "cold": "Chamber {n}: temperature is too low ({temp} C). Please check.",
    "ml": "Chamber {n}: temperature pattern looks unusual ({temp} C). Please check.",
}
EXTRA_EN = {"comp_off": "Compressor appears to be OFF for {minutes} min."}
EXTRA_HI = {"comp_off": "कंप्रेसर {minutes} मिनट से बंद लग रहा है।"}

VOICE_HI = "नमस्ते। चैंबर {n} में तापमान ख़तरे के स्तर पर है। इस कॉल को स्वीकार करने के लिए 1 दबाएँ।"
FARMER_WEEKLY_HI = "नमस्ते {name} जी, आपका {bags} बोरी माल चैंबर {n} में सुरक्षित है। अब तक का औसत तापमान {avg}°C है। चिंता की कोई बात नहीं। अपना रिकॉर्ड देखें: {url}"
FARMER_ALARM_HI = "नमस्ते {name} जी, चैंबर {n} में कुछ देर के लिए तापमान बढ़ गया है। हमारी टीम ठीक कर रही है। आपको अपडेट मिलता रहेगा।"


def build_alert(reasons, chamber_id, chamber_temp=None):
    """reasons: [(code, params)] -> (code, hindi, english)."""
    by_code = {c: p for c, p in reasons}
    for code in PRIORITY:
        if code in by_code:
            p = dict(by_code[code]); p.setdefault("temp", chamber_temp); p["n"] = chamber_id
            hi, en = HI[code].format(**p), EN[code].format(**p)
            if "comp_off" in by_code:
                hi += " " + EXTRA_HI["comp_off"].format(**by_code["comp_off"])
                en += " " + EXTRA_EN["comp_off"].format(**by_code["comp_off"])
            return code, hi, en
    return "none", "", ""

OFFLINE_HI = "चैंबर {n} का सेंसर {minutes} मिनट से डेटा नहीं भेज रहा। बिजली, वाई-फ़ाई या सेंसर जाँचें।"
OFFLINE_EN = "Chamber {n}: sensor has sent no data for {minutes} min. Check power, Wi-Fi or the sensor."
MAINT_HI = "चैंबर {n}: '{task}' का समय हो गया है। कृपया टेक्नीशियन से करवाएँ।"
