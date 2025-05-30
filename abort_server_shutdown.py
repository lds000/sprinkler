from flask import Flask

app = Flask(__name__)

@app.route('/abort-shutdown')
def abort_shutdown():
    # Return "abort" to prevent shutdown, or "ok" to allow it
    return "ok"  # Change to "abort" when you don't want to allow shutdown

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
    