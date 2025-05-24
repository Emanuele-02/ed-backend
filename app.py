from flask import Flask, request, jsonify, session
from flask_cors import CORS
from openai import OpenAI
import os

# Inizializzazione Flask
app = Flask(__name__)
CORS(app)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "super-secret-key")

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
    data = request.get_json()
    message = data.get("message", "")
    is_subscribed = data.get("isSubscribed", False)

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

    # Aggiungi il messaggio dell'utente alla history
    session["history"].append({"role": "user", "content": message})

    # Prompt modificato per includere la richiesta del titolo
    modified_prompt = SYSTEM_PROMPT + "\n\nDopo ogni risposta, fornisci anche un titolo breve (massimo 6 parole) che riassuma l’argomento trattato. Scrivi alla fine su una riga separata: Titolo: ..."

    messages = [{"role": "system", "content": modified_prompt}]
    messages.extend(session["history"][-10:])

    try:
        response = client.chat.completions.create(
            model="gpt-4-0125-preview",
            messages=messages
        )

        full_reply = response.choices[0].message.content

        # Estrai il titolo dalla risposta
        lines = full_reply.strip().split("\n")
        title_line = next((line for line in reversed(lines) if line.strip().lower().startswith("titolo:")), None)
        title = title_line.split(":", 1)[1].strip() if title_line else "Nuova chat"

        # Rimuovi la riga del titolo dalla risposta da mostrare all’utente
        reply_clean = "\n".join(line for line in lines if not line.strip().lower().startswith("titolo:")).strip()

        # Salva la risposta nell'history
        session["history"].append({"role": "assistant", "content": reply_clean})

        return jsonify({
            "reply": reply_clean,
            "title": title,
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
