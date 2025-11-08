# app.py
from __future__ import annotations
import os, re, json, uuid, hashlib, base64, time
from urllib.parse import urlencode
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_from_directory, Response
from jinja2 import TemplateNotFound
from pathlib import Path
import stripe

from db import db_init, db_upsert_bot, db_get_bot
from mail import send_lead_email
from bot import (
    parse_contact_info, build_system_prompt, call_llm_with_history,
    extract_lead_json, _lead_from_history, rule_based_next_question
)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")

# cookies / iframe
SESSION_SECURE = os.getenv("SESSION_SECURE", "true").lower() == "true"
app.config.update(SESSION_COOKIE_SAMESITE="None", SESSION_COOKIE_SECURE=SESSION_SECURE)

# CONFIG
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
PRICE_ID = os.getenv("STRIPE_PRICE_ID", "").strip()
BASE_URL = (os.getenv("BASE_URL", "http://127.0.0.1:5000")).rstrip("/")
app.jinja_env.globals["BASE_URL"] = BASE_URL

DEFAULT_LEAD_EMAIL = os.getenv("DEFAULT_LEAD_EMAIL", "").strip()

db_init()  # ensure schema exists

# small in-memory demo bots
BOTS = {
    "avocat-001":  {"pack":"avocat","name":"Betty Bot (Avocat)","color":"#4F46E5","avatar_file":"avocat.jpg","profile":{},"greeting":"","buyer_email":None,"owner_name":None,"public_id":None},
    "immo-002":    {"pack":"immo","name":"Betty Bot (Immobilier)","color":"#16A34A","avatar_file":"immo.jpg","profile":{},"greeting":"","buyer_email":None,"owner_name":None,"public_id":None},
    "medecin-003": {"pack":"medecin","name":"Betty Bot (Médecin)","color":"#0284C7","avatar_file":"medecin.jpg","profile":{},"greeting":"","buyer_email":None,"owner_name":None,"public_id":None},
    "spectra-demo": {
        "pack": "avocat",
        "name": "Betty Bot (Spectra Media)",
        "color": "#4F46E5",
        "avatar_file": "avocat.jpg",
        "profile": {},
        "greeting": "Bonjour et bienvenue chez Spectra Media. Souhaitez-vous créer votre Betty Bot métier ?",
        "buyer_email": None,
        "owner_name": "Spectra Media",
        "public_id": "spectra-demo"
    },
}

CONVS = {}

def static_url(filename: str) -> str:
    return url_for("static", filename=filename)

def _gen_public_id(email: str, bot_key: str) -> str:
    h = hashlib.sha1((email + "|" + bot_key).encode()).hexdigest()[:8]
    return f"{bot_key}-{h}"

def find_bot_by_public_id(public_id: str):
    if not public_id:
        return None, None
    bot = db_get_bot(public_id)
    if bot:
        return bot.get("bot_key"), bot
    parts = public_id.split("-")
    if len(parts) < 3:
        for k, b in BOTS.items():
            if b.get("public_id") == public_id:
                b2 = dict(b); b2["bot_key"] = k; b2["public_id"] = public_id
                return k, b2
        return None, None
    bot_key = "-".join(parts[:2])
    b = BOTS.get(bot_key)
    if not b:
        return None, None
    b2 = dict(b); b2["bot_key"] = bot_key; b2["public_id"] = public_id
    return bot_key, b2

def _slug_from_pack(pack: str) -> str:
    pack = (pack or "").lower()
    return {"agent_immobilier":"immo", "immobilier":"immo", "avocat":"avocat", "medecin":"medecin"}.get(pack, "immo")

# ---------- Routes de confort / assets ----------
@app.route("/api/ping")
def api_ping():
    return jsonify({"ok": True, "time": int(time.time())}), 200

@app.route("/healthz")
def healthz():
    return "ok", 200

@app.route("/favicon.ico")
def favicon_root():
    p = os.path.join(app.root_path, "static", "favicon.ico")
    if os.path.exists(p):
        return send_from_directory(os.path.dirname(p), os.path.basename(p))
    return "", 204

@app.route("/favicon.png")
def favicon_png():
    p = os.path.join(app.root_path, "static", "favicon.png")
    if os.path.exists(p):
        return send_from_directory(os.path.dirname(p), os.path.basename(p))
    return "", 204

@app.route("/favicon-16x16.png")
def fav16():
    p = os.path.join(app.root_path, "static", "favicon-16x16.png")
    if os.path.exists(p):
        return send_from_directory(os.path.dirname(p), os.path.basename(p))
    return "", 204

@app.route("/favicon-32x32.png")
def fav32():
    p = os.path.join(app.root_path, "static", "favicon-32x32.png")
    if os.path.exists(p):
        return send_from_directory(os.path.dirname(p), os.path.basename(p))
    return "", 204

@app.route("/site.webmanifest")
def site_manifest():
    p = os.path.join(app.root_path, "static", "site.webmanifest")
    if os.path.exists(p):
        return send_from_directory(os.path.dirname(p), os.path.basename(p))
    return jsonify({"name":"Betty Bots","short_name":"Betty","icons":[]}), 200

@app.route("/avatar/<slug>")
def avatar(slug: str):
    static_dir = os.path.join(app.root_path, "static")
    filename = f"logo-{slug}.jpg"
    path = os.path.join(static_dir, filename)
    if os.path.exists(path):
        return send_from_directory(static_dir, filename)
    transparent_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+Xad8AAAAASUVORK5CYII="
    )
    return Response(transparent_png, mimetype="image/png")

# ---------- Pages ----------
@app.route("/")
def index():
    try:
        return render_template("index.html", title="Découvrez Betty")
    except TemplateNotFound:
        return """
        <!doctype html><meta charset="utf-8">
        <style>
          body{margin:0;background:#0b0f1e;color:#e8ecff;font:16px/1.5 system-ui,Segoe UI,Roboto,Inter,sans-serif;display:grid;place-items:center;height:100vh}
          .card{background:#12172a;border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:28px;max-width:720px;box-shadow:0 10px 30px rgba(0,0,0,.25)}
          h1{margin:0 0 12px;font-size:22px}
          code{background:#0b1222;padding:2px 6px;border-radius:6px}
          a{color:#8ab4ff;text-decoration:none}
          .muted{color:#a8b2c8}
        </style>
        <div class="card">
          <h1>Betty Bots — déploiement minimal</h1>
          <p class="muted">Le template <code>templates/index.html</code> n’a pas été trouvé.<br>Fallback pour éviter le crash.</p>
          <p>Interface de config : <a href="/config">/config</a></p>
        </div>
        """, 200

@app.route("/config", methods=["GET", "POST"])
def config_page():
    if request.method == "POST":
        pack      = request.form.get("pack", "avocat")
        color     = request.form.get("color", "#4F46E5")
        avatar    = request.form.get("avatar", "avocat.jpg")
        greeting  = request.form.get("greeting", "")
        contact   = request.form.get("contact_info", "")
        persona_x = request.form.get("persona_x", "0")
        persona_y = request.form.get("persona_y", "0")
        return redirect(url_for("inscription_page",
                                pack=pack, color=color, avatar=avatar,
                                greeting=greeting, contact=contact,
                                px=persona_x, py=persona_y))
    try:
        return render_template("config.html", title="Configurer votre bot")
    except TemplateNotFound:
        return """
        <!doctype html><meta charset="utf-8">
        <h1>Configurer votre bot</h1>
        <form method="post">
          <label>Pack <select name="pack">
            <option value="avocat">Avocat</option>
            <option value="medecin">Médecin</option>
            <option value="immo">Immobilier</option>
          </select></label><br><br>
          <label>Couleur <input name="color" value="#4F46E5"></label><br><br>
          <label>Avatar <input name="avatar" value="avocat.jpg"></label><br><br>
          <label>Message d'accueil <input name="greeting" value=""></label><br><br>
          <label>Infos contact (nom, email, tel, horaires...)<br>
            <textarea name="contact_info" rows="4" cols="50"></textarea>
          </label><br><br>
          <button type="submit">Continuer</button>
        </form>
        """, 200

@app.route("/inscription", methods=["GET", "POST"])
def inscription_page():
    if request.method == "POST":
        import stripe  # local alias ok
        email   = request.form.get("email")
        pack    = request.args.get("pack", "avocat")
        color   = request.args.get("color", "#4F46E5")
        avatar  = request.args.get("avatar", "avocat.jpg")
        greet   = request.args.get("greeting", "") or ""
        contact = request.args.get("contact", "") or ""
        px      = request.args.get("px", "0")
        py      = request.args.get("py", "0")

        profile = parse_contact_info(contact)
        bot_id = "avocat-001" if pack == "avocat" else ("medecin-003" if pack == "medecin" else "immo-002")
        base = BOTS[bot_id]

        public_id = _gen_public_id(email or str(uuid.uuid4()), bot_id)

        bot_db = {
            "public_id": public_id,
            "bot_key": bot_id,
            "pack": base["pack"],
            "name": base["name"],
            "color": color or base["color"],
            "avatar_file": avatar or base["avatar_file"],
            "greeting": greet or "",
            "buyer_email": email,
            "owner_name": (email.split("@")[0].title() if email else "Client"),
            "profile": profile,
        }
        db_upsert_bot(bot_db)

        if not stripe.api_key or not PRICE_ID:
            return redirect(f"{BASE_URL}/recap?pack={pack}&public_id={public_id}&session_id=fake_checkout_dev", code=303)

        session_obj = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": PRICE_ID, "quantity": 1}],
            customer_email=email,
            success_url=f"{BASE_URL}/recap?pack={pack}&public_id={public_id}&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{BASE_URL}/inscription?pack={pack}&color={color}&avatar={avatar}",
            metadata={
                "pack": pack, "color": color, "avatar": avatar,
                "greeting": greet, "contact_info": contact,
                "persona_x": px, "persona_y": py,
                "public_id": public_id
            }
        )
        return redirect(session_obj.url, code=303)
    try:
        return render_template("inscription.html", title="Inscription")
    except TemplateNotFound:
        return """
        <!doctype html><meta charset="utf-8">
        <h1>Inscription</h1>
        <form method="post">
          <label>Email <input type="email" name="email" required></label><br><br>
          <button type="submit">Aller au paiement</button>
        </form>
        """, 200

@app.route("/recap")
def recap_page():
    pack = (request.args.get("pack") or "").strip().lower() or "avocat"
    public_id = (request.args.get("public_id") or "").strip()

    bot = db_get_bot(public_id) if public_id else None
    if not bot:
        key = "avocat-001" if pack == "avocat" else ("medecin-003" if pack == "medecin" else "immo-002")
        base = BOTS[key]
        bot = {
            "public_id": public_id or f"{key}-demo",
            "name": base["name"],
            "owner_name": "Client",
            "buyer_email": "",
            "pack": base["pack"],
            "color": base["color"],
            "avatar_file": base["avatar_file"],
            "greeting": ""
        }

    display_name = bot.get("name") or "Betty Bot"
    owner        = bot.get("owner_name") or ""
    full_name    = f"{display_name} — {owner}" if owner else display_name

    slug = _slug_from_pack(bot.get("pack") or pack)
    avatar_file = bot.get("avatar_file") or f"logo-{slug}.jpg"

    params = {"public_id": bot.get("public_id"), "embed": "1"}
    buyer = (bot.get("buyer_email") or "").strip()
    if buyer: params["buyer_email"] = buyer
    embed_url = f"{BASE_URL}/chat?{urlencode(params)}"

    iframe_snippet = (
        '<div style="position:relative;width:100%;max-width:420px;height:620px;margin:0 auto;">\n'
        f'  <iframe src="{embed_url}" title="{full_name}" '
        'style="width:100%;height:100%;border:0;border-radius:16px;'
        'box-shadow:0 10px 30px rgba(0,0,0,.25);background:#0b0f1e;" '
        'loading="lazy" referrerpolicy="no-referrer-when-downgrade" '
        'allow="clipboard-read; clipboard-write; microphone; autoplay"></iframe>\n'
        '</div>'
    )

    cfg = {
        "pack":        bot.get("pack") or pack,
        "color":       bot.get("color") or "#4F46E5",
        "greeting":    bot.get("greeting") or "Bonjour, je suis Betty. Comment puis-je vous aider ?",
        "contact":     (bot.get("profile") or {}).get("raw") or "",
        "px":          request.args.get("px") if request.args.get("px") is not None else "0.5",
        "py":          request.args.get("py") if request.args.get("py") is not None else "0.5",
        "avatar_url":  static_url(avatar_file),
        "public_id":   bot.get("public_id") or "",
        "buyer_email": bot.get("buyer_email") or "",
        "display_name": display_name,
        "owner_name":   owner,
        "full_name":    full_name,
        "embed_url":    embed_url,
        "iframe_snippet": iframe_snippet,
    }

    try:
        return render_template("recap.html", title="Récapitulatif", cfg=cfg, info=cfg, base_url=BASE_URL, full_name=full_name)
    except TemplateNotFound:
        return f"""<!doctype html><meta charset="utf-8">
        <h1>Récapitulatif — {full_name}</h1>
        <p>Code d’intégration :</p>
        <pre style="white-space:pre-wrap;border:1px solid #ccc;padding:10px;border-radius:8px">{cfg["iframe_snippet"]}</pre>
        <p>Prévisualisation :</p>
        <div style="max-width:420px">{cfg["iframe_snippet"]}</div>
        """, 200

@app.route("/chat")
def chat_page():
    public_id = (request.args.get("public_id") or "").strip()
    embed     = request.args.get("embed", "0") == "1"
    buyer_email = request.args.get("buyer_email", "").strip()

    bot = db_get_bot(public_id)
    if not bot:
        base = BOTS["avocat-001"]
        bot = {
            "public_id": public_id or "avocat-001-demo",
            "name": base["name"],
            "color": base["color"],
            "avatar_file": base["avatar_file"],
            "greeting": "Bonjour, je suis Betty. Comment puis-je vous aider ?",
            "owner_name": "Client",
            "profile": {},
            "pack": base["pack"]
        }

    display_name = bot.get("name") or "Betty Bot"
    pack_code = (bot.get("pack") or "").lower()
    pack_label_map = {"medecin":"Médecin","avocat":"Avocat","immo":"Immobilier","immobilier":"Immobilier","notaire":"Notaire"}
    pack_label = pack_label_map.get(pack_code, "")
    full_name = display_name if "(" in display_name else (f"{display_name} ({pack_label})" if pack_label else display_name)

    try:
        return render_template(
            "chat.html",
            title="Betty — Chat",
            base_url=BASE_URL,
            public_id=bot.get("public_id") or "",
            full_name=full_name,
            header_title="Betty Bot, votre assistante AI",
            color=bot.get("color") or "#4F46E5",
            avatar_url=static_url(bot.get("avatar_file") or "avocat.jpg"),
            greeting=bot.get("greeting") or "Bonjour, je suis Betty. Comment puis-je vous aider ?",
            buyer_email=buyer_email,
            embed=embed
        )
    except TemplateNotFound:
        return f"""<!doctype html><meta charset="utf-8">
        <h1>{full_name}</h1>
        <div style="position:relative;width:100%;max-width:420px;height:620px;margin:0 auto;border:1px solid #ddd;border-radius:12px;overflow:hidden">
          <iframe src="{BASE_URL}/api/ping" style="width:100%;height:100%;border:0"></iframe>
        </div>""", 200

# ---------- API ----------
@app.route("/api/bettybot", methods=["POST"])
def bettybot_reply():
    payload    = request.get_json(force=True, silent=True) or {}
    user_input = (payload.get("message") or "").strip()
    public_id  = (payload.get("bot_id") or payload.get("public_id") or "").strip()
    conv_id    = (payload.get("conv_id") or "").strip()

    if not user_input:
        return jsonify({"response": "Dites-moi ce dont vous avez besoin 🙂"}), 200

    bot_key, bot = find_bot_by_public_id(public_id)
    if not bot:
        bot_key = "avocat-001"
        bot = BOTS[bot_key]

    # history
    history = (CONVS.get(conv_id, []) if conv_id else session.get(f"conv_{public_id or bot_key}", []))[-6:]

    # resolve buyer email
    buyer_email_ctx = (
        (payload.get("buyer_email") or "").strip()
        or ((db_get_bot(public_id) or {}).get("buyer_email") if public_id else "")
        or (bot or {}).get("buyer_email")
        or os.getenv("DEFAULT_LEAD_EMAIL", "").strip()
    )
    app.logger.info(f"[DBG] buyer_email={buyer_email_ctx!r} pid='{public_id}'")

    demo_mode = (public_id == "spectra-demo")
    system_prompt = "..." if demo_mode else build_system_prompt(bot.get("pack", "avocat"), bot.get("profile", {}), bot.get("greeting", ""))

    full_text = call_llm_with_history(system_prompt=system_prompt, history=history, user_input=user_input)
    if not full_text:
        full_text = rule_based_next_question(bot.get("pack",""), history + [{"role":"user","content": user_input}])

    response_text, lead = extract_lead_json(full_text)
    response_text = re.sub(r"<LEAD_JSON>.*?</LEAD_JSON>\s*$", "", response_text or "", flags=re.DOTALL).rstrip()

    # update history
    history.append({"role": "user", "content": user_input})
    history.append({"role": "assistant", "content": response_text})
    if conv_id: CONVS[conv_id] = history
    else:       session[f"conv_{public_id or bot_key}"] = history

    # send email if ready
    if True:
        if not lead or not isinstance(lead, dict):
            lead = _lead_from_history(history + [{"role": "user", "content": user_input}])

        stage_ok = bool(lead.get("phone")) and bool(lead.get("name")) and bool(lead.get("email"))
        if stage_ok:
            buyer_email = buyer_email_ctx
            if not buyer_email:
                app.logger.warning(f"[LEAD] buyer_email introuvable pour bot_id={public_id or 'N/A'} ; email non envoyé.")
            else:
                try:
                    send_lead_email(
                        to_email=buyer_email,
                        lead={
                            "reason": lead.get("reason", ""),
                            "name": lead.get("name", ""),
                            "email": lead.get("email", ""),
                            "phone": lead.get("phone", ""),
                            "availability": lead.get("availability", ""),
                            "stage": "ready",
                        },
                        bot_name=(bot or {}).get("name") or "Betty Bot",
                    )
                    app.logger.info(f"[LEAD] Email envoyé à {buyer_email} pour bot {public_id}")
                except Exception as e:
                    app.logger.exception(f"[LEAD] Erreur envoi email -> {e}")

    return jsonify({"response": response_text, "stage": (lead or {}).get("stage") if lead else None})

@app.route("/api/embed_meta")
def embed_meta():
    public_id = (request.args.get("public_id") or "").strip()
    if not public_id:
        return jsonify({"error": "missing public_id"}), 400
    _, bot = find_bot_by_public_id(public_id)
    if not bot:
        return jsonify({"error": "bot_not_found"}), 404
    return jsonify({
        "bot_id": public_id,
        "owner_name": bot.get("owner_name") or "Client",
        "display_name": bot.get("name") or "Betty Bot",
        "color_hex": bot.get("color") or "#4F46E5",
        "avatar_url": static_url(bot.get("avatar_file") or "avocat.jpg"),
        "greeting": bot.get("greeting") or "Bonjour, je suis Betty. Comment puis-je vous aider ?",
        "buyer_email": bot.get("buyer_email") or ""
    })

@app.route("/api/bot_meta")
def bot_meta():
    bot_id = (request.args.get("bot_id") or request.args.get("public_id") or "").strip()
    if bot_id == "spectra-demo":
        b = BOTS["spectra-demo"]
        return jsonify({
            "name": "Betty Bot (Spectra Media)",
            "color_hex": b.get("color") or "#4F46E5",
            "avatar_url": static_url(b.get("avatar_file") or "avocat.jpg"),
            "greeting": b.get("greeting") or "Bonjour et bienvenue chez Spectra Media. Souhaitez-vous créer votre Betty Bot métier ?"
        })
    if bot_id in BOTS:
        b = BOTS[bot_id]
        demo_greetings = {
            "avocat-001":  "Bonjour et bienvenue au cabinet Werner & Werner. Que puis-je faire pour vous ?",
            "immo-002":    "Bonjour et bienvenue à l’agence Werner Immobilier. Comment puis-je vous aider ?",
            "medecin-003": "Bonjour et bienvenue au cabinet Werner Santé. Que puis-je faire pour vous ?",
        }
        return jsonify({
            "name": b.get("name") or "Betty Bot",
            "color_hex": b.get("color") or "#4F46E5",
            "avatar_url": static_url(b.get("avatar_file") or "avocat.jpg"),
            "greeting": demo_greetings.get(bot_id, "Bonjour, je suis Betty. Comment puis-je vous aider ?")
        })
    _, bot = find_bot_by_public_id(bot_id)
    if not bot:
        return jsonify({"error": "bot_not_found"}), 404
    return jsonify({
        "name": bot.get("name") or "Betty Bot",
        "color_hex": bot.get("color") or "#4F46E5",
        "avatar_url": static_url(bot.get("avatar_file") or "avocat.jpg"),
        "greeting": bot.get("greeting") or "Bonjour, je suis Betty. Comment puis-je vous aider ?"
    })

@app.route("/api/reset", methods=["POST"])
def reset_conv():
    key = (request.get_json(silent=True) or {}).get("key")
    if key and key in CONVS:
        CONVS.pop(key, None)
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
