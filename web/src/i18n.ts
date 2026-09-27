// UI chrome labels. Answers themselves come localized from the backend.
import type { Level } from "./types";

export const LANGS = [
  { code: "auto", label: "Auto" },
  { code: "en", label: "English" },
  { code: "hi", label: "हिन्दी" },
  { code: "ta", label: "தமிழ்" },
  { code: "te", label: "తెలుగు" },
  { code: "ml", label: "മലയാളം" },
] as const;

type Dict = Record<string, string>;

const L: Record<string, Dict> = {
  en: {
    risingFrom: "{factor} → {level} from {time}",
    f_wave_height: "Waves", f_wind_speed: "Wind", f_weather_code: "Thunderstorm", f_visibility: "Visibility", f_advisory: "Official warning", from: "from", recommended: "Recommended route", direct: "Direct line", timeInHigh: "time in HIGH/SEVERE", noRoute: "No safe route — do not depart",
    LOW: "Low", MODERATE: "Moderate", HIGH: "High", SEVERE: "Severe", INSUFFICIENT_DATA: "Cannot confirm",
    placeholder: "Ask about sea safety, fishing zones, routes, alerts…", send: "Send", why: "Why?", safety: "Safety",
    route: "Route", alerts: "Alerts", evidence: "Evidence", trace: "Agents", sources: "Sources", zones: "Zones",
    timeline: "Risk over time", lowest: "Lowest-risk window", none: "None in this window", overall: "Overall risk",
    listen: "Listen", speak: "Speak", location: "Location", useGps: "Use GPS", simulated: "SIMULATED DATA",
    live: "LIVE DATA", fallback: "LIVE SOURCE DOWN — SIMULATED", conditions: "Conditions",
    micListening: "Listening… just pause when you are done", micWorking: "Writing down what you said…", micError: "Could not catch that — please speak again or type", micDenied: "Allow the microphone to ask by voice",
  },
  hi: {
    risingFrom: "{factor} → {time} बजे से जोखिम {level}",
    f_wave_height: "लहरें", f_wind_speed: "हवा", f_weather_code: "आँधी/बिजली", f_visibility: "दृश्यता", f_advisory: "आधिकारिक चेतावनी", from: "से", recommended: "सुझाया गया मार्ग", direct: "सीधा मार्ग", timeInHigh: "अधिक/गंभीर जोखिम में समय", noRoute: "कोई सुरक्षित मार्ग नहीं — यात्रा न करें",
    LOW: "कम", MODERATE: "मध्यम", HIGH: "अधिक", SEVERE: "गंभीर", INSUFFICIENT_DATA: "पुष्टि नहीं",
    placeholder: "समुद्र की सुरक्षा, मत्स्य क्षेत्र, मार्ग, चेतावनी के बारे में पूछें…", send: "भेजें", why: "क्यों?",
    safety: "सुरक्षा", route: "मार्ग", alerts: "चेतावनी", evidence: "प्रमाण", trace: "एजेंट", sources: "स्रोत",
    zones: "क्षेत्र", timeline: "समय के साथ जोखिम", lowest: "सबसे कम जोखिम का समय", none: "इस अवधि में नहीं",
    overall: "कुल जोखिम", listen: "सुनें", speak: "बोलें", location: "स्थान", useGps: "GPS", simulated: "सिम्युलेटेड डेटा",
    live: "लाइव डेटा", fallback: "लाइव स्रोत बंद — सिम्युलेटेड", conditions: "स्थिति",
    micListening: "सुन रहे हैं… बात पूरी होने पर बस रुक जाएँ", micWorking: "आपकी बात लिख रहे हैं…", micError: "बात समझ नहीं आई — फिर से बोलें या लिखें", micDenied: "आवाज़ से पूछने के लिए माइक्रोफ़ोन की अनुमति दें",
  },
  ta: {
    risingFrom: "{factor} → {time} முதல் அபாயம் {level}",
    f_wave_height: "அலைகள்", f_wind_speed: "காற்று", f_weather_code: "இடி/மின்னல்", f_visibility: "தெரிவுநிலை", f_advisory: "அதிகாரப்பூர்வ எச்சரிக்கை", from: "முதல்", recommended: "பரிந்துரைக்கப்பட்ட வழி", direct: "நேரடி வழி", timeInHigh: "அதிக/கடுமையான அபாயத்தில் நேரம்", noRoute: "பாதுகாப்பான வழி இல்லை — புறப்பட வேண்டாம்",
    LOW: "குறைவு", MODERATE: "மிதமான", HIGH: "அதிகம்", SEVERE: "கடுமையான", INSUFFICIENT_DATA: "உறுதிசெய்ய இயலாது",
    placeholder: "கடல் பாதுகாப்பு, மீன்பிடி மண்டலம், வழி, எச்சரிக்கை பற்றிக் கேளுங்கள்…", send: "அனுப்பு",
    why: "ஏன்?", safety: "பாதுகாப்பு", route: "வழி", alerts: "எச்சரிக்கை", evidence: "ஆதாரம்", trace: "முகவர்கள்",
    sources: "மூலங்கள்", zones: "மண்டலங்கள்", timeline: "நேரப்படி அபாயம்", lowest: "குறைந்த அபாய நேரம்",
    none: "இந்த நேரத்தில் இல்லை", overall: "மொத்த அபாயம்", listen: "கேள்", speak: "பேசு", location: "இடம்",
    useGps: "GPS", simulated: "உருவகப்படுத்தப்பட்ட தரவு", live: "நேரடி தரவு", fallback: "நேரடி மூலம் இல்லை — உருவகம்",
    conditions: "நிலை",
    micListening: "கேட்கிறது… பேசி முடித்ததும் நிறுத்துங்கள்", micWorking: "நீங்கள் சொன்னதை எழுதுகிறது…", micError: "புரியவில்லை — மீண்டும் பேசவும் அல்லது தட்டச்சு செய்யவும்", micDenied: "குரலில் கேட்க மைக்ரோஃபோன் அனுமதி தேவை",
  },
  te: {
    risingFrom: "{factor} → {time} నుండి ప్రమాదం {level}",
    f_wave_height: "అలలు", f_wind_speed: "గాలి", f_weather_code: "ఉరుములు/పిడుగులు", f_visibility: "దృశ్యత", f_advisory: "అధికారిక హెచ్చరిక", from: "నుండి", recommended: "సూచించిన మార్గం", direct: "నేరు మార్గం", timeInHigh: "ఎక్కువ/తీవ్ర ప్రమాదంలో సమయం", noRoute: "సురక్షిత మార్గం లేదు — బయలుదేరవద్దు",
    LOW: "తక్కువ", MODERATE: "మధ్యస్థ", HIGH: "ఎక్కువ", SEVERE: "తీవ్ర", INSUFFICIENT_DATA: "నిర్ధారించలేము",
    placeholder: "సముద్ర భద్రత, చేపల వేట ప్రాంతం, మార్గం, హెచ్చరికల గురించి అడగండి…", send: "పంపు", why: "ఎందుకు?",
    safety: "భద్రత", route: "మార్గం", alerts: "హెచ్చరికలు", evidence: "ఆధారం", trace: "ఏజెంట్లు", sources: "మూలాలు",
    zones: "ప్రాంతాలు", timeline: "సమయానుసార ప్రమాదం", lowest: "తక్కువ ప్రమాద సమయం", none: "ఈ సమయంలో లేదు",
    overall: "మొత్తం ప్రమాదం", listen: "వినండి", speak: "మాట్లాడండి", location: "ప్రదేశం", useGps: "GPS",
    simulated: "అనుకరణ డేటా", live: "లైవ్ డేటా", fallback: "లైవ్ మూలం లేదు — అనుకరణ", conditions: "పరిస్థితి",
    micListening: "వింటోంది… మాట్లాడటం పూర్తయ్యాక ఆగండి", micWorking: "మీరు చెప్పింది రాస్తోంది…", micError: "అర్థం కాలేదు — మళ్లీ మాట్లాడండి లేదా టైప్ చేయండి", micDenied: "వాయిస్‌తో అడగడానికి మైక్రోఫోన్ అనుమతి ఇవ్వండి",
  },
  ml: {
    risingFrom: "{factor} → {time} മുതൽ അപകടസാധ്യത {level}",
    f_wave_height: "തിരമാല", f_wind_speed: "കാറ്റ്", f_weather_code: "ഇടിമിന്നൽ", f_visibility: "ദൃശ്യപരത", f_advisory: "ഔദ്യോഗിക മുന്നറിയിപ്പ്", from: "മുതൽ", recommended: "നിർദ്ദേശിച്ച പാത", direct: "നേരിട്ടുള്ള പാത", timeInHigh: "ഉയർന്ന/അതിഗുരുതര അപകടത്തിലെ സമയം", noRoute: "സുരക്ഷിത പാതയില്ല — യാത്ര തുടങ്ങരുത്",
    LOW: "കുറവ്", MODERATE: "മിതമായ", HIGH: "ഉയർന്ന", SEVERE: "അതിഗുരുതരം", INSUFFICIENT_DATA: "ഉറപ്പാക്കാനാവില്ല",
    placeholder: "കടൽ സുരക്ഷ, മത്സ്യബന്ധന മേഖല, പാത, മുന്നറിയിപ്പ് എന്നിവയെക്കുറിച്ച് ചോദിക്കുക…", send: "അയയ്ക്കുക",
    why: "എന്തുകൊണ്ട്?", safety: "സുരക്ഷ", route: "പാത", alerts: "മുന്നറിയിപ്പ്", evidence: "തെളിവ്", trace: "ഏജന്റുകൾ",
    sources: "ഉറവിടങ്ങൾ", zones: "മേഖലകൾ", timeline: "സമയാനുസൃത അപകടസാധ്യത", lowest: "ഏറ്റവും കുറഞ്ഞ അപകട സമയം",
    none: "ഈ സമയത്ത് ഇല്ല", overall: "മൊത്തം അപകടസാധ്യത", listen: "കേൾക്കുക", speak: "സംസാരിക്കുക", location: "സ്ഥാനം",
    useGps: "GPS", simulated: "സിമുലേറ്റ് ചെയ്ത ഡാറ്റ", live: "ലൈവ് ഡാറ്റ", fallback: "ലൈവ് ഉറവിടം ലഭ്യമല്ല — സിമുലേഷൻ",
    conditions: "സാഹചര്യം",
    micListening: "കേൾക്കുന്നു… പറഞ്ഞു കഴിഞ്ഞാൽ നിർത്തുക", micWorking: "നിങ്ങൾ പറഞ്ഞത് എഴുതുന്നു…", micError: "മനസ്സിലായില്ല — വീണ്ടും പറയുക അല്ലെങ്കിൽ ടൈപ്പ് ചെയ്യുക", micDenied: "ശബ്ദത്തിലൂടെ ചോദിക്കാൻ മൈക്രോഫോൺ അനുമതി നൽകുക",
  },
};

export function tr(lang: string, key: string): string {
  return (L[lang] ?? L.en)[key] ?? L.en[key] ?? key;
}

export function factorLabel(lang: string, variable: string | null | undefined): string {
  if (!variable) return "—";
  const key = `f_${variable}`;
  const v = tr(lang, key);
  return v === key ? variable.replace(/_/g, " ") : v;
}

/** Numeric value worth showing next to a factor (weather codes and warnings have none). */
export function factorValue(variable: string | null | undefined, value: unknown, unit: string | null | undefined): string {
  if (typeof value !== "number" || variable === "weather_code" || variable === "advisory") return "";
  return ` ${value.toFixed(1)} ${unit ?? ""}`.trimEnd();
}

export function levelLabel(lang: string, level: Level | string | null | undefined): string {
  return level ? tr(lang, level) : "—";
}

export const SPEECH_LOCALE: Record<string, string> = {
  en: "en-IN", hi: "hi-IN", ta: "ta-IN", te: "te-IN", ml: "ml-IN", mr: "mr-IN", kn: "kn-IN", gu: "gu-IN", bn: "bn-IN", or: "or-IN",
};
