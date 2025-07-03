from flask import Flask, request, jsonify, session
from flask_cors import CORS
from openai import OpenAI
import os
import stripe
import requests

# Inizializzazione Flask
app = Flask(__name__)
CORS(app, origins=["https://www.ed.lume.study"])
app.secret_key = os.getenv("FLASK_SECRET_KEY", "super-secret-key")

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

WP_API_URL = "https://www.ed.lume.study/wp-json/lume/v1/set_subscribed"
WP_API_SECRET = os.getenv("WP_API_SECRET", "your-fallback-secret")
print("🔐 ENV WP_API_SECRET:", repr(WP_API_SECRET))

# Inizializzazione OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Prompt di sistema per Ed
SYSTEM_PROMPT = """
Sei Ed, un tutor digitale per studenti della scuola secondaria di primo e secondo grado (medie e superiori). Il tuo compito è aiutare lo studente a comprendere meglio le materie scolastiche, spiegando concetti in modo chiaro, graduale e accessibile.
Ti rivolgi allo studente con tono amichevole, mai infantile, mostrando rispetto e incoraggiamento.

✅ Quando spieghi:
- Utilizzi un linguaggio semplice ma preciso.
- Adatti la difficoltà delle spiegazioni al livello scolastico (medie o superiori).
- Fornisci esempi concreti, pratici e legati alla vita reale o al contesto scolastico.
- Se il tema lo richiede, proponi analogie visive o schematiche.

✅ Se lo studente sbaglia:
- Correggi con delicatezza, facendo notare l’errore senza umiliarlo.
- Riformuli la spiegazione per renderla più chiara.
- Inviti lo studente a riprovare, proponendo piccoli esercizi guidati.

✅ Se lo studente chiede solo una risposta:
- Fornisci la risposta, ma spieghi sempre il perché e il come si arriva alla soluzione.

✅ Materie principali:
- Italiano, Matematica, Storia, Geografia, Scienze, Inglese

✅ Cosa non fai:
- Non fai i compiti senza spiegare.
- Non usi gergo tecnico senza spiegazione.
- Non dai risposte secche.

✅ Stile:
- Empatico, rassicurante, autorevole.
- Preferisci il dialogo attivo.
- Sempre positivo e incoraggiante.

Il tuo scopo è insegnare a capire, non solo a rispondere.
"""

@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json()
        conversation_id = data.get("conversationId")
        message = data.get("message", "")
        is_subscribed = data.get("isSubscribed", False)
        method = data.get("method", "Esplicativo")  # 🆕 metodo didattico scelto

        if not message:
            return jsonify({"error": "Messaggio mancante"}), 400

        if not is_subscribed:
            if "free_count" not in session:
                session["free_count"] = 0
            session["free_count"] += 1
            if session["free_count"] > 5:
                return jsonify({"showStripe": True})

        if "history" not in session:
            session["history"] = []

        session["history"].append({"role": "user", "content": message})

        # 🧠 Prompt base + adattamento in base al metodo
        method_instructions = {
            "Esplicativo": "Spiega in modo chiaro e lineare, come un bravo insegnante.",
            "Interrogativo": "Rispondi stimolando la riflessione con domande continue e provocatorie.",
            "Socratico": "Accompagna lo studente alla scoperta con domande guidate, senza mai dare risposte dirette.",
            "Esemplificativo": "Spiega sempre usando esempi pratici e concreti.",
            "Operativo": "Dai istruzioni pratiche passo-passo o proponi esercizi guidati."
        }

        method_prompt = method_instructions.get(method, method_instructions["Esplicativo"])

        combined_prompt = SYSTEM_PROMPT + "\n\n" + method_prompt + "\n\nDopo ogni risposta, fornisci anche un titolo breve (massimo 6 parole) che riassuma l’argomento trattato. Scrivi alla fine su una riga separata: Titolo: ..."

        messages = [{"role": "system", "content": combined_prompt}]
        messages.extend(session["history"][-10:])

        response = client.chat.completions.create(
            model="gpt-4-0125-preview",
            messages=messages
        )

        full_reply = response.choices[0].message.content

        lines = full_reply.strip().split("\n")
        title_line = next((line for line in reversed(lines) if line.strip().lower().startswith("titolo:")), None)
        title = title_line.split(":", 1)[1].strip() if title_line else "Nuova chat"

        title_lower = title.lower()
        if (
            title_lower.startswith("ciao") or
            "posso aiutarti" in title_lower or
            "nuova chat" in title_lower or
            len(title) < 4
        ):
            title = "Nuova chat"

        reply_clean = "\n".join(
            line for line in lines if not line.strip().lower().startswith("titolo:")
        ).strip()

        session["history"].append({"role": "assistant", "content": reply_clean})

        return jsonify({
            "reply": reply_clean,
            "title": title,
            "conversationId": conversation_id,
            "free_count": session.get("free_count", 0)
        })

    except Exception as e:
        import traceback
        print("❌ Errore backend:", e)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/reset", methods=["POST"])
def reset():
    session.pop("history", None)
    session.pop("free_count", None)
    return jsonify({"status": "Memoria resettata"})

@app.route("/create-subscription", methods=["POST"])
print("📥 Richiesta ricevuta su /create-subscription")
def create_subscription():
    auth_header = request.headers.get("Authorization")

    if not auth_header or not auth_header.startswith("Bearer "):
        print("❌ Header mancante o malformato:", auth_header)
        return jsonify({"error": "Unauthorized"}), 401

    expected_token = f"Bearer {WP_API_SECRET}"
    received_token = auth_header.strip()

    print("🔑 Token ricevuto:", repr(received_token))
    print("🔐 Token atteso:", repr(expected_token))

    if received_token != expected_token:
        print("❌ Token non valido!")
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    print("📦 JSON ricevuto:", data)
    email = data.get("email")
    payment_method_id = data.get("payment_method_id")

    try:
        print("🔄 Creazione customer e abbonamento Stripe in corso...")
        
        customer = stripe.Customer.create(email=email, payment_method=payment_method_id, invoice_settings={
            "default_payment_method": payment_method_id,
        })

        subscription = stripe.Subscription.create(
            customer=customer.id,
            items=[{"price": "price_1RWfGcHUcdjxDHrPD8Fmy9hS"}],
            expand=["latest_invoice.payment_intent"]
        )

        payment_intent = subscription["latest_invoice"]["payment_intent"]
        
        print("✅ Abbonamento Stripe creato:", subscription.id)
        print("💳 Payment status:", payment_intent["status"])
        
        if payment_intent["status"] == "succeeded":
            wp_response = requests.post(WP_API_URL, json={"email": email}, headers={
                "Authorization": f"Bearer {WP_API_SECRET}"
            })
            print("\u2705 WP response:", wp_response.text)
            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "error": "Pagamento non riuscito"})

    except Exception as e:
        print("❌ Errore durante la creazione abbonamento:", str(e))
        return jsonify({"success": False, "error": str(e)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
    
