from flask import Flask, request, jsonify, session
from flask_cors import CORS
from openai import OpenAI
import os
import stripe
import requests
import logging

# ——— CONFIGURA LOGGING STRUTTURATO ———
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# ——— INIZIALIZZAZIONE FLASK ———
app = Flask(__name__)
CORS(app, resources={
    r"/*": {
        "origins": [
            "https://www.ed.lume.study",
            "https://ed.lume.study",
            "http://localhost:3000"
        ],
        "supports_credentials": True
    }
})
app.secret_key = os.getenv("FLASK_SECRET_KEY", "super-secret-key")

# ——— STRIPE & API ———
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
WP_API_URL = "https://www.ed.lume.study/wp-json/lume/v1/set_subscribed"
WP_API_SECRET = os.getenv("WP_API_SECRET", "your-fallback-secret")
logging.debug("🔐 ENV WP_API_SECRET: %r", WP_API_SECRET)

# ——— OPENAI ———
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ——— PROMPT SISTEMA ———
SYSTEM_PROMPT = """
Sei Ed, un tutor digitale per studenti della scuola secondaria di primo e secondo grado (medie e superiori)...[TRONCATO per spazio, lascia il tuo completo qui]
"""

# ——— ROUTE CHAT ———
@app.route("/chat", methods=["POST", "OPTIONS"])
def chat():
    try:
        data = request.get_json()
        conversation_id = data.get("conversationId")
        message = data.get("message", "")
        is_subscribed = data.get("isSubscribed", False)
        method = data.get("method", "Esplicativo")

        if not message:
            return jsonify({"error": "Messaggio mancante"}), 400

        if not is_subscribed:
            session["free_count"] = session.get("free_count", 0) + 1
            if session["free_count"] > 5:
                return jsonify({"showStripe": True})

        session.setdefault("history", [])
        session["history"].append({"role": "user", "content": message})

        method_prompt = {
            "Esplicativo": "Spiega in modo chiaro e lineare, come un bravo insegnante.",
            "Interrogativo": "Rispondi stimolando la riflessione con domande continue e provocatorie.",
            "Socratico": "Accompagna lo studente alla scoperta con domande guidate, senza mai dare risposte dirette.",
            "Esemplificativo": "Spiega sempre usando esempi pratici e concreti.",
            "Operativo": "Dai istruzioni pratiche passo-passo o proponi esercizi guidati."
        }.get(method, "Spiega in modo chiaro e lineare, come un bravo insegnante.")

        combined_prompt = SYSTEM_PROMPT + "\n\n" + method_prompt + "\n\nDopo ogni risposta, fornisci anche un titolo breve (massimo 6 parole) che riassuma l’argomento trattato. Scrivi alla fine su una riga separata: Titolo: ..."

        messages = [{"role": "system", "content": combined_prompt}]
        messages.extend(session["history"][-10:])

        response = client.chat.completions.create(
            model="gpt-4-0125-preview",
            messages=messages
        )

        full_reply = response.choices[0].message.content
        lines = full_reply.strip().split("\n")
        title_line = next((line for line in reversed(lines) if line.lower().startswith("titolo:")), None)
        title = title_line.split(":", 1)[1].strip() if title_line else "Nuova chat"

        if len(title) < 4 or "ciao" in title.lower() or "nuova chat" in title.lower():
            title = "Nuova chat"

        reply_clean = "\n".join(
            line for line in lines if not line.lower().startswith("titolo:")
        ).strip()

        session["history"].append({"role": "assistant", "content": reply_clean})

        return jsonify({
            "reply": reply_clean,
            "title": title,
            "conversationId": conversation_id,
            "free_count": session.get("free_count", 0)
        })

    except Exception as e:
        logging.exception("❌ Errore nel backend /chat")
        return jsonify({"error": str(e)}), 500

# ——— RESET ———
@app.route("/reset", methods=["POST"])
def reset():
    session.pop("history", None)
    session.pop("free_count", None)
    return jsonify({"status": "Memoria resettata"})

# ——— CREAZIONE ABBONAMENTO STRIPE ———
@app.route("/create-subscription", methods=["POST"])
def create_subscription():
    logging.info("📥 Richiesta ricevuta su /create-subscription")

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        logging.warning("❌ Header mancante o malformato: %s", auth_header)
        return jsonify({"error": "Unauthorized"}), 401

    received_token = auth_header.replace("Bearer ", "").strip()
    expected_token = WP_API_SECRET.strip()

    if received_token != expected_token:
        logging.warning("❌ Token non valido!")
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    email = data.get("email")
    payment_method_id = data.get("payment_method_id")
    logging.info("📦 JSON ricevuto: %s", data)

    try:
        logging.info("🔄 Creazione customer e abbonamento Stripe...")

        customer = stripe.Customer.create(
            email=email,
            payment_method=payment_method_id,
            invoice_settings={"default_payment_method": payment_method_id}
        )

        subscription = stripe.Subscription.create(
            customer=customer.id,
            items=[{"price": "price_1RWfGcHUcdjxDHrPD8Fmy9hS"}],
            expand=["latest_invoice.payment_intent"]
        )

        invoice = subscription.get("latest_invoice")
        if not invoice:
            logging.error("❌ Nessuna invoice trovata nella subscription.")
            return jsonify({"success": False, "error": "Nessuna invoice trovata."})

        payment_intent = invoice.get("payment_intent")
        if not payment_intent or not isinstance(payment_intent, dict):
            logging.error("❌ Nessun PaymentIntent valido.")
            return jsonify({"success": False, "error": "Nessun PaymentIntent trovato."})

        client_secret = payment_intent.get("client_secret")
        status = payment_intent.get("status")
        logging.info("💳 PaymentIntent status: %s", status)

        # Se il pagamento è già riuscito, aggiorna WordPress subito
        if status == "succeeded":
            wp_response = requests.post(
                WP_API_URL,
                json={"email": email},
                headers={"Authorization": f"Bearer {WP_API_SECRET}"}
            )
            logging.info("✅ WP response: %s", wp_response.text)
            
        # In ogni caso, ritorna il client_secret per completare il pagamento lato frontend    
        return jsonify({
            "success": True,
            "subscription_id": subscription.id,
            "client_secret": client_secret,
            "status": status,
        }) 
                
    except Exception as e:
        logging.exception("❌ Errore durante la creazione abbonamento")
        return jsonify({"success": False, "error": str(e)})

# ——— CREAZIONE ABBONAMENTO STRIPE ———
@app.route("/create-subscription", methods=["POST"])
def create_subscription():
    ...
except Exception as e:
   logging.exception("❌ Errore durante la creazione abbonamento") 
    return jsonify({"success": False, "error": str(e)})

# ——— CONFERMA ABBONAMENTO DOPO PAGAMENTO ———
@app.route("/confirm-subscription", methods=["POST"])
def confirm_subscription():
    try:
        data = request.get_json()
        emanil: = data.get("email")

        if not email:
            return jsonify({"success": False, "error": "Email mancante"}), 400

        wp_response = requests.post(
            WP_API_URL,
            json={"email": email},
            headers={"Authorization": f"Bearer {WP_API_SECRET}"}
        )

        logging.info("✅ WP aggiornato da /confirm-subscription: %s", wp_response.text)
        return jsonify({"success": True})

    except Exception as e:
        logging.exception("❌ Errore in /confirm-subscription")
        return jsonify({"success": False, "error": str(e)})

# ——— AVVIO APP ———
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)

    
