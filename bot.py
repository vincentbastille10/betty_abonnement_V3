# bot.py
from __future__ import annotations
import os, re, yaml, json, time, requests

TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY", "").strip()
TOGETHER_API_URL = "https://api.together.xyz/v1/chat/completions"
LLM_MODEL = os.getenv("LLM_MODEL", "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo").strip()
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "180"))

def parse_contact_info(raw: str) -> dict:
    raw = (raw or "").strip()
    if not raw:
        return {"raw": "", "name": "", "email": "", "phone": "", "address": "", "hours": ""}
    m_email = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', raw)
    email = m_email.group(0) if m_email else ""
    m_phone = re.search(r'(\+?\d[\d \.\-]{6,})', raw)
    phone = m_phone.group(1).strip() if m_phone else ""
    m_hours = re.search(r'(horaire|heures?|ouvertures?)\s*[:\-]?\s*(.+)', raw, re.I)
    hours = m_hours.group(2).strip() if m_hours else ""
    m_name = re.search(r'(?:nom|entreprise|cabinet)\s*[:\-]?\s*(.+)', raw, re.I)
    name = m_name.group(1).strip() if m_name else ""
    m_addr = re.search(r'(?:adresse|address)\s*[:\-]?\s*(.+)', raw, re.I)
    address = m_addr.group(1).strip() if m_addr else ""
    return {"raw": raw, "name": name, "email": email, "phone": phone, "address": address, "hours": hours}

def build_business_block(profile: dict) -> str:
    if not profile:
        return ""
    lines = ["\n---\nINFORMATIONS ETABLISSEMENT (utilise-les dans tes réponses) :"]
    if profile.get("name"):    lines.append(f"• Nom : {profile['name']}")
    if profile.get("phone"):   lines.append(f"• Téléphone : {profile['phone']}")
    if profile.get("email"):   lines.append(f"• Email : {profile['email']}")
    if profile.get("address"): lines.append(f"• Adresse : {profile['address']}")
    if profile.get("hours"):   lines.append(f"• Horaires : {profile['hours']}")
    lines.append("---\n")
    return "\n".join(lines)

def build_system_prompt(pack_name: str, profile: dict, greeting: str = "") -> str:
    base = (
        "Tu es l'assistante AI du professionnel. Ta mission prioritaire est de QUALIFIER TRÈS VITE "
        "(2 échanges maximum avant de demander les coordonnées), puis de proposer un rappel."
    )
    path = f"data/packs/{pack_name}.yaml"
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                data = yaml.safe_load(f) or {}
            base = data.get("prompt", base)
        except Exception:
            pass
    biz  = build_business_block(profile)
    guide = """
RÈGLES OBLIGATOIRES (communes à TOUS les métiers) :
- Style : clair, 1 à 2 phrases max par message. Une seule question à la fois.
- Après 1–2 phrases de mise en contexte maximum, collecte IMMÉDIATEMENT :
  1) « Quel est votre numéro de téléphone ? »
  2) « Quel est votre nom et prénom complets ? »
  3) « Quelle est votre adresse e-mail ? »
- Dès que téléphone + nom complet + e-mail sont collectés, écris : 
  « Parfait, je transmets vos coordonnées. Vous serez rappelé rapidement. »
- N’affiche jamais de variables (pas de {{...}}) ni de JSON à l’écran.

BALISE TECHNIQUE (dernière ligne, une seule ligne, sans markdown) :
<LEAD_JSON>{"reason":"", "name":"", "email":"", "phone":"", "availability":"", "stage":"collecting|ready"}</LEAD_JSON>

RAPPEL :
- Le JSON doit tenir sur UNE ligne. 
- Passe "stage" à "ready" UNIQUEMENT quand téléphone + nom complet + email sont présents (peu importe le métier).
"""
    greet = f"\nMessage d'accueil recommandé : {greeting}\n" if greeting else ""
    return f"{base}\n{biz}\n{guide}\n{greet}"

def call_llm_with_history(system_prompt: str, history: list, user_input: str) -> str:
    if not TOGETHER_API_KEY:
        return ""
    headers = {"Authorization": f"Bearer {TOGETHER_API_KEY}", "Content-Type": "application/json"}
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": user_input})
    payload = {"model": LLM_MODEL, "max_tokens": LLM_MAX_TOKENS, "temperature": 0.4, "messages": messages}
    backoffs = [0.6, 1.2, 2.4, 4.8]
    last_err_text = None
    for wait in backoffs:
        try:
            r = requests.post(TOGETHER_API_URL, headers=headers, json=payload, timeout=30)
            if r.ok:
                data = r.json()
                content = (data.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()
                if content:
                    return content
                last_err_text = "Réponse vide du modèle."
            else:
                try:
                    err = r.json()
                    last_err_text = f"{err.get('error',{}).get('message') or err}"
                except Exception:
                    last_err_text = f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            last_err_text = f"{type(e).__name__}: {e}"
        time.sleep(wait)
    print("[LLM][Together][FAIL]", last_err_text or "unknown")
    return ""

LEAD_TAG_RE = re.compile(r"`?\s*<LEAD_JSON>\s*(\{.*?\})\s*</LEAD_JSON>\s*`?\s*$", re.DOTALL)

def extract_lead_json(text: str):
    if not text:
        return text, None
    m = LEAD_TAG_RE.search(text)
    if not m:
        return text, None
    lead_raw = m.group(1).strip()
    message = text[:m.start()].rstrip()
    try:
        lead = json.loads(lead_raw)
    except Exception:
        lead = None
    return message, lead

def _lead_from_history(history: list) -> dict:
    user_text = " ".join([m["content"] for m in history if m.get("role") == "user"]) or ""
    d = {"reason": "", "email": "", "phone": "", "name": "", "availability": "", "stage": "collecting"}
    if not user_text:
        return d
    m = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', user_text)
    if m: d["email"] = m.group(0)
    m = re.search(r'(\+?\d[\d \.\-]{6,})', user_text)
    if m: d["phone"] = m.group(1).strip()
    m = re.search(r"(?:je m(?:'|e)appelle|nom\s*:?)\s*([A-Za-zÀ-ÖØ-öø-ÿ'\-\s]{2,80})", user_text, re.I)
    if m: d["name"] = m.group(1).strip()
    m = re.search(r'(?:souhaite|veux|voudrais|besoin|motif|pour)\s*:?(.{5,140})', user_text, re.I)
    if m: d["reason"] = m.group(1).strip()
    m = re.search(r'(demain|matin|après-midi|soir|lundi|mardi|mercredi|jeudi|vendredi)[^\.!?]{0,60}', user_text, re.I)
    if m: d["availability"] = m.group(0).strip()
    if d["phone"] and d["name"] and d["email"]:
        d["stage"] = "ready"
    return d

def rule_based_next_question(pack: str, history: list) -> str:
    lead = _lead_from_history(history)
    if not lead["phone"]:
        msg = "Quel est votre numéro de téléphone ?"
    elif not lead["name"]:
        msg = "Quel est votre nom et prénom complets ?"
    elif not lead["email"]:
        msg = "Quelle est votre adresse e-mail ?"
    else:
        msg = "Parfait, je transmets vos coordonnées. Vous serez rappelé rapidement."
        lead["stage"] = "ready"
    return f"{msg}\n<LEAD_JSON>{json.dumps(lead, ensure_ascii=False)}</LEAD_JSON>"
