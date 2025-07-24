import os
import time
import logging
import threading
import pandas as pd
import math
import traceback
from binance.client import Client
from binance.exceptions import BinanceAPIException
from flask import Flask, render_template_string, request, redirect, url_for, session, jsonify
from db import init_db, salvar_operacao, buscar_operacoes
from datetime import datetime

from werkzeug.middleware.proxy_fix import ProxyFix

# ================= CONFIGURAÇÃO ================= #



# Validação de variáveis de ambiente
API_KEY = os.getenv('BINANCE_API_KEY')
API_SECRET = os.getenv('BINANCE_API_SECRET')
if not API_KEY or not API_SECRET:
    raise ValueError("API_KEY ou API_SECRET não configurados no .env")

# Parâmetros configuráveis via .env
SYMBOL = os.getenv('SYMBOL', 'ETHUSDT')
INTERVAL = os.getenv('INTERVAL', '1m')
PROFIT_PERC = float(os.getenv('PROFIT_PERC', 0.0050))
LOSS_PERC = float(os.getenv('LOSS_PERC', 0.0045))
GALE = [float(x) for x in os.getenv('GALE', '0.006,0.012,0.024,0.048,0.096').split(',')]
MAX_GALE = int(os.getenv('MAX_GALE', 5))  # Limite de iterações do gale
EMERGENCY_STOP_LOSSES = int(os.getenv('EMERGENCY_STOP_LOSSES', 5))  # Parada de emergência após X perdas

LOSS_FILE = 'loss_orders.txt'
LOG_FILE = 'log.txt'

# Lock para acesso a arquivos e status
file_lock = threading.Lock()
status_lock = threading.Lock()

# ================= LOGGING ================= #

formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler = logging.FileHandler(LOG_FILE)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)

logging.getLogger().setLevel(logging.INFO)
logging.getLogger().addHandler(file_handler)

flask_logger = logging.getLogger('werkzeug')
flask_logger.setLevel(logging.INFO)
flask_logger.addHandler(file_handler)

logging.info("Bot iniciado...")

client = Client(API_KEY, API_SECRET)
server_time = client.get_server_time()['serverTime']
local_time = int(time.time() * 1000)
client.timestamp_offset = server_time - local_time

# ================= FLASK APP ================= #
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
app.secret_key = os.getenv('FLASK_SECRET_KEY', os.urandom(24))  # Chave secreta para sessões

# Template HTML com Bootstrap
TEMPLATE = """
<!doctype html>
<html lang="pt-br">
  <head>
    <meta charset="utf-8">
    <title>Bot Binance</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
      body { padding-top: 40px; }
      pre { background-color: #f8f9fa; padding: 1em; border-radius: 5px; max-height: 400px; overflow-y: auto; }
        .progress-container {
            width: 80%;
            margin: 40px auto;
        }
        .progress-bar.positivo {
            background-color: #4caf50 !important; /* verde */
        }

        .progress-bar.negativo {
        background-color: #f44336 !important; /* vermelho */
        }
        .progress-text {
            position: absolute;
            left: 50%;
            top: 50%;
            transform: translate(-50%, -50%);
            color: #fff;
            font-weight: bold;
        }
        .scroll-tabela {
            max-height: 450px; /* ajuste conforme sua necessidade */
            overflow-y: auto;
        }
         thead th {
            position: sticky;
            top: 0;
            background-color: #0d6efd; /* cor da classe table-primary */
            color: white;
            z-index: 1;
        }

    </style>
  </head>
  <body>
    <div class="container">
      <h1 class="mb-4">🤖 Bistequera Bot - Binance Futures</h1>
      {% if not session.get('autenticado') %}
        <form method="post" action="/login">
          <div class="mb-3">
            <label for="password" class="form-label">Senha:</label>
            <input type="password" class="form-control" id="password" name="password">
          </div>
          <button type="submit" class="btn btn-primary">Login</button>
        </form>
      {% else %}
        <div class="row mb-4">
            <!-- COLUNA ESQUERDA - INFORMAÇÕES E BOTÃO -->
            <div class="col-md-6">
                <div class="mb-4 d-flex justify-content-end">
                    <button class="btn btn-outline-secondary" type="button" data-bs-toggle="collapse" data-bs-target="#configuracoes" aria-expanded="false">
                        ⚙️ Configurações
                    </button>
                </div>
                <div class="collapse" id="configuracoes">
                    <div class="card card-body mb-4">
                        <form action="/atualizar_config" method="post">
                            <div class="row">
                                <div class="mb-3">
                                    <label for="symbols" class="form-label">Selecione até 5 Pares de Símbolos</label>
                                    <select id="symbols" name="symbols" class="form-select" required>
                                        <option value="BTCUSDT">BTC/USDT</option>
                                        <option value="ETHUSDT">ETH/USDT</option>
                                        <option value="BNBUSDT">BNB/USDT</option>
                                        <option value="SOLUSDT">SOL/USDT</option>
                                        <option value="ADAUSDT">ADA/USDT</option>
                                    </select>
                                    <div class="invalid-feedback">Você deve selecionar entre 1 e 5 pares.</div>
                                </div>

                                <div class="mb-3">
                                    <label for="timeframe" class="form-label">Time Frame</label>
                                    <select id="timeframe" name="timeframe" class="form-select" required>
                                        <option value="">Selecione</option>
                                        <option value="1m">1m</option>
                                        <option value="5m">5m</option>
                                        <option value="15m">15m</option>
                                        <option value="1h">1h</option>
                                        <option value="4h">4h</option>
                                        <option value="1d">1d</option>
                                    </select>
                                    <div class="invalid-feedback">Selecione um time frame válido.</div>
                                </div>

                                <div class="mb-3">
                                    <label for="gale" class="form-label">Gales (ex: 0.006, 0.012, 0.024, 0.048)</label>
                                    <input type="text" id="gale" name="gale" class="form-control" value="0.006,0.012,0.024,0.048,0.096" required pattern="^(\\d+(\\.\\d+)?)(,\\d+(\\.\\d+)?)*$">
                                    <div class="invalid-feedback">Informe os Gales no formato correto, separados por vírgula. Ex: 1.5,2,2.5</div>
                                </div>
                                <div class="mb-3">
                                    <label>Max Gale</label>
                                    <input name="max_gale" class="form-control" value="{{ max_gale }}">
                                </div>
                                <div class="mb-3">
                                    <label>Emergency Stop (Losses)</label>
                                    <input name="emergency_stop" class="form-control" value="{{ emergency_stop }}">
                                </div>
                            </div>
                            <button type="submit" class="btn btn-success mt-3">Salvar Configurações</button>
                        </form>
                    </div>
                </div>
                <ul class="list-group mb-3">
                    <li class="list-group-item"><strong>Preço Atual:</strong> <span id="preco-atual">{{ preco_atual }}</span></li>
                    <li class="list-group-item"><strong>Preço de Entrada:</strong> <span id="posicao">{{ posicao }}</span></li>
                    <li class="list-group-item"><strong>Quantidade:</strong> <span id="quantidade">{{ quantidade }}</span></li>
                    <li class="list-group-item"><strong>Direção:</strong> <span id="direcao">{{ direcao }}</span></li>
                    <li class="list-group-item"><strong>Alvo: </strong> <span id="alvo">{{ alvo }}</span> | <strong>Stop:</strong> <span id="stop">{{ stop }}</span></li>
                    <li class="list-group-item"><strong>Sequência de Gales:</strong> <span id="gales">{{ gales }}</span></li>
                    <li class="list-group-item"><strong>Quantidade de Losses:</strong> <span id="losses">{{ losses }}</span></li>
                </ul>
                <div class="col-md-12 d-flex flex-column align-items-center justify-content-center">
                    <h5>Andamento da Posição</h5>
                    <div class="w-100 mb-3 position-relative">
                        <div class="progress" style="height: 30px; background-color: #f1f1f1; position: relative;">
                            <div id="progress-bar"
                                class="progress-bar {{ 'bg-success' if progresso_percentual >= 0 else 'bg-danger' }}"
                                role="progressbar"
                                style="width: {{ progresso_percentual | abs }}%;"
                                aria-valuenow="{{ progresso_percentual }}"
                                aria-valuemin="0"
                                aria-valuemax="100">
                                <span class="progress-text" style="
                                    position: absolute;
                                    left: 50%;
                                    top: 50%;
                                    transform: translate(-50%, -50%);
                                    color: white;
                                    font-weight: bold;
                                    user-select: none;
                                    pointer-events: none;">
                                {{ progresso_percentual }}%
                                </span>
                            </div>
                        </div>
                    </div>
                </div>
                <form action="/forcar_fechamento" method="post" class="mb-2">
                <button class="btn btn-danger btn-lg w-100" type="submit">🚨 Forçar Fechamento</button>
                </form>
                <a href="/logout" class="btn btn-secondary btn-lg w-100">Logout</a>
            </div>

            <!-- COLUNA DIREITA - GRÁFICO -->
            <div class="card col-md-6 d-flex flex-column align-items-center justify-content-center">
                <div class="card-header bg-primary text-white d-flex justify-content-between align-items-center">
                    <div>
                        <span class="me-2"><strong>Gains:</strong> <span id="gains">{{ gains }}</span></span>
                        <span class="me-2"><strong>Losses:</strong> <span id="losses">{{ losses }}</span></span>
                        <span><strong>Lucro Total:</strong> <span id="profit_total">{{ profit_total }}</span> USDT</span>
                        <span><strong>Taxa de Acerto:</strong> <span id="taxa_acerto">{{ taxa_acerto }}</span>%</span>
                    </div>
                </div>
                <div class="scroll-tabela">
                    <div class="card-body">
                        <table class="table table-sm table-bordered">
                            <thead class="table-primary text-white">
                                <tr>
                                <th>Data</th>
                                <th>Entrada</th>
                                <th>Saída</th>
                                <th>Lado</th>
                                <th>Qtd</th>
                                <th>ROI</th>
                                <th>Resultado</th>
                                <th>Lucro USDT</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for op in operacoes %}
                                <tr>
                                <td>{{ op[1] }}</td>
                                <td>{{ op[2] }}</td>
                                <td>{{ op[3] }}</td>
                                <td>{{ op[4] }}</td>
                                <td>{{ op[5] }}</td>
                                <td>{{ op[7] }}%</td>
                                <td>{{ op[6] }}</td>
                                <td>{{ op[8] }}</td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
            
        </div>
      {% endif %}
    </div>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.min.js"></script>
    <script>
        function atualizarBarra(porcentagem) {
            const barra = document.getElementById('progress-bar');
            const valor = Math.min(Math.abs(porcentagem), 100);
            barra.style.width = valor + '%';
            barra.classList.remove('positivo', 'negativo');

            if (porcentagem >= 0) {
                barra.classList.add('positivo');
            } else {
                barra.classList.add('negativo');
            }

            // Atualiza texto interno da barra
            barra.querySelector('span.progress-text').innerText = porcentagem.toFixed(2) + '%';
        }

        function atualizarStatus() {
            fetch('/status_json')
            .then(res => res.json())
            .then(data => {
                if (!data.error) {
                document.getElementById('preco-atual').innerText = data.preco_atual;
                document.getElementById('posicao').innerText = data.posicao;
                document.getElementById('quantidade').innerText = data.quantidade;
                document.getElementById('direcao').innerText = data.direcao;
                document.getElementById('alvo').innerText = data.alvo;
                document.getElementById('stop').innerText = data.stop;
                document.getElementById('losses').innerText = data.losses;
                document.getElementById('gales').innerText = data.gales.join(', ');
                atualizarBarra(data.progresso_percentual);
                }
            })
            .catch(console.error);
        }

        document.getElementById('gains').innerText = data.gains;
        document.getElementById('losses').innerText = data.losses;
        document.getElementById('profit_total').innerText = data.profit_total.toFixed(2);
        document.getElementById('taxa_acerto').innerText = data.taxa_acerto.toFixed(2);

        // Atualiza a cada 3 segundos
        setInterval(atualizarStatus, 3000);

        // Atualiza ao carregar a página
        document.addEventListener('DOMContentLoaded', () => {
        atualizarStatus();
        });

        document.addEventListener("DOMContentLoaded", function () {
        const configForm = document.getElementById("configForm");
        const symbolSelect = document.getElementById("symbols");

        // Limita a seleção a no máximo 5 símbolos
        symbolSelect.addEventListener("change", function () {
            if ([...symbolSelect.selectedOptions].length > 5) {
            alert("Você só pode selecionar até 5 pares.");
            [...symbolSelect.options].forEach(option => option.selected = false);
            }
        });

        configForm.addEventListener("submit", function (e) {
            if (!configForm.checkValidity()) {
            e.preventDefault();
            e.stopPropagation();

    </script>
  </body>
</html>
"""

status_bot = {
    "preco_atual": "---",
    "preco": "---",
    "posicao": "---",
    "quantidade": "---",
    "direcao": "---",
    "log": "Iniciando bot...",
    "losses": "---",
    "gales": "---",
    "preco_fechamento": "---",
    "quantidade_fechamento": "---",
    "lucro_usdt": "---"
}

@app.route('/')
def index():
    if not session.get('autenticado'):
        return render_template_string(TEMPLATE)
    try:
        with file_lock:
            with open(LOG_FILE, "r") as f:
                log_content = ''.join(f.readlines()[-30:])
    except FileNotFoundError:
        log_content = "Sem logs disponíveis."

    with status_lock:
        contexto = dict(status_bot)
        contexto["operacoes"] = buscar_operacoes()
        contexto["log"] = log_content
        contexto["losses"] = read_loss_count()
        contexto["gales"] = GALE
        direcao = contexto.get("direcao", "").lower()
        preco_atual = contexto.get("preco_atual")
        preco_entrada = contexto.get("posicao")

        operacoes = buscar_operacoes()
        total_gains, total_losses, profit_total, taxa_acerto = calcular_resumo_operacoes(operacoes)

        contexto["operacoes"] = operacoes
        contexto["gains"] = total_gains
        contexto["losses"] = total_losses
        contexto["profit_total"] = profit_total
        contexto["taxa_acerto"] = taxa_acerto
        if isinstance(preco_atual, (int, float)) and isinstance(preco_entrada, (int, float)) and preco_entrada != 0:
            tipo = direcao
            if tipo in ['long', 'short']:
                alvo = calcula_alvo(preco_entrada, tipo)
                stop = calcula_stop(preco_entrada, tipo)

                if tipo == 'long' and alvo != preco_entrada and stop != preco_entrada:
                    if preco_atual >= preco_entrada:
                        progresso = (preco_atual - preco_entrada) / (alvo - preco_entrada)
                    else:
                        progresso = -((preco_entrada - preco_atual) / (preco_entrada - stop))

                elif tipo == 'short' and alvo != preco_entrada and stop != preco_entrada:
                    if preco_atual <= preco_entrada:
                        progresso = (preco_entrada - preco_atual) / (preco_entrada - alvo)
                    else:
                        progresso = -((preco_atual - preco_entrada) / (stop - preco_entrada))
                else:
                    progresso = 0

                progresso_percentual = round(progresso * 100, 2)
                contexto["progresso_percentual"] = progresso_percentual
            else:
                contexto["progresso_percentual"] = 0.0
        else:
            contexto["progresso_percentual"] = 0.0

        if isinstance(preco_entrada, (int, float)) and direcao in ['long', 'short']:
            contexto["alvo"] = round(calcula_alvo(preco_entrada, direcao), 4)
            contexto["stop"] = round(calcula_stop(preco_entrada, direcao), 4)
        else:
            contexto["alvo"] = "---"
            contexto["stop"] = "---"

    contexto.update({
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "profit_perc": PROFIT_PERC,
        "loss_perc": LOSS_PERC,
        "gale": ','.join(map(str, GALE)),
        "max_gale": MAX_GALE,
        "emergency_stop": EMERGENCY_STOP_LOSSES
    })

    return render_template_string(TEMPLATE, **contexto)

@app.route('/login', methods=['POST'])
def login():
    password = request.form.get('password')
    if password == os.getenv('BOT_PASSWORD', 'admin123'):  # Senha configurável via .env
        session['autenticado'] = True
        return redirect(url_for('index'))
    return "Senha incorreta", 401

@app.route('/logout')
def logout():
    session.pop('autenticado', None)
    return redirect(url_for('index'))

@app.route('/forcar_fechamento', methods=['POST'])
def forcar_fechamento():
    try:
        # Obter dados da posição atual com segurança
        with status_lock:
            qtd = status_bot.get("quantidade", 0)
            tipo = status_bot.get("direcao", "").lower()
            preco_entrada = status_bot.get("preco_entrada")

        if qtd <= 0 or tipo not in ['long', 'short'] or not preco_entrada:
            logging.warning("❌ Dados insuficientes para forçar fechamento")
            return redirect('/')
        
        # Envia ordem de fechamento
        fechar_posicao(qtd, tipo)

        return redirect('/')

    except Exception as e:
        logging.error("❌ Erro ao forçar fechamento:")
        logging.error(traceback.format_exc())
        return "Erro ao forçar fechamento", 500
    

@app.route('/atualizar_config', methods=['POST'])
def atualizar_config():
    global SYMBOL, INTERVAL, PROFIT_PERC, LOSS_PERC, GALE, MAX_GALE, EMERGENCY_STOP_LOSSES

    try:
        SYMBOL = request.form.get('symbol', SYMBOL)
        INTERVAL = request.form.get('interval', INTERVAL)
        PROFIT_PERC = float(request.form.get('profit_perc', PROFIT_PERC))
        LOSS_PERC = float(request.form.get('loss_perc', LOSS_PERC))
        GALE = [float(x) for x in request.form.get('gale', ','.join(map(str, GALE))).split(',')]
        MAX_GALE = int(request.form.get('max_gale', MAX_GALE))
        EMERGENCY_STOP_LOSSES = int(request.form.get('emergency_stop', EMERGENCY_STOP_LOSSES))

        logging.info(f"🔧 Parâmetros atualizados via interface")
        return redirect(url_for('index'))
    except Exception as e:
        logging.error(f"Erro ao atualizar configs: {e}")
        return "Erro ao atualizar configurações", 500

@app.route('/status_json')
def status_json():
    if not session.get('autenticado'):
        return jsonify({"error": "Não autorizado"}), 401

    with status_lock:
        preco_entrada = status_bot.get("posicao", "---")
        direcao = status_bot.get("direcao", "---").lower()
        if preco_entrada != "---" and direcao in ['long', 'short']:
            alvo = round(calcula_alvo(preco_entrada, direcao), 4)
            stop = round(calcula_stop(preco_entrada, direcao), 4)
        else:
            alvo = "---"
            stop = "---"

        # Cálculo do progresso (igual à lógica que você já tem)
        preco_atual = status_bot.get("preco_atual", 0)
        progresso_percentual = 0.0
        if isinstance(preco_atual, (int, float)) and isinstance(preco_entrada, (int, float)) and preco_entrada != "---":
            tipo = direcao
            if tipo in ['long', 'short']:
                if tipo == 'long' and alvo != preco_entrada and stop != preco_entrada:
                    if preco_atual >= preco_entrada:
                        progresso = (preco_atual - preco_entrada) / (alvo - preco_entrada)
                    else:
                        progresso = -((preco_entrada - preco_atual) / (preco_entrada - stop))
                elif tipo == 'short' and alvo != preco_entrada and stop != preco_entrada:
                    if preco_atual <= preco_entrada:
                        progresso = (preco_entrada - preco_atual) / (preco_entrada - alvo)
                    else:
                        progresso = -((preco_atual - preco_entrada) / (stop - preco_entrada))
                else:
                    progresso = 0
                progresso_percentual = round(progresso * 100, 2)

        operacoes = buscar_operacoes()
        gains, losses, profit_total, taxa_acerto = calcular_resumo_operacoes(operacoes)

        data = {
            "preco_atual": preco_atual,
            "posicao": preco_entrada,
            "quantidade": status_bot.get("quantidade", "---"),
            "direcao": status_bot.get("direcao", "---"),
            "alvo": alvo,
            "stop": stop,
            "progresso_percentual": progresso_percentual,
            "losses": read_loss_count(),
            "gales": GALE,
            "gains": gains,
            "profit_total": profit_total,
            "taxa_acerto": taxa_acerto
        }

    return jsonify(data)

@app.route('/logs')
def logs():
    if not session.get('autenticado'):
        return "Acesso não autorizado", 401
    try:
        with file_lock:
            with open(LOG_FILE, "r") as f:
                log_content = ''.join(f.readlines()[-50:])
    except FileNotFoundError:
        log_content = "Sem logs disponíveis."
    return log_content

# ================= FUNÇÕES AUXILIARES ================= #

def init_loss_file():
    with file_lock:
        if not os.path.exists(LOSS_FILE):
            with open(LOSS_FILE, 'w') as f:
                f.write("0\n")

def read_loss_count():
    try:
        with file_lock:
            with open(LOSS_FILE, 'r') as f:
                return sum(float(line.strip()) for line in f if line.strip().replace('.', '', 1).isdigit())
    except:
        return 0

def write_loss():
    with file_lock:
        with open(LOSS_FILE, 'a') as f:
            f.write("1\n")

def clear_loss():
    with file_lock:
        with open(LOSS_FILE, 'w') as f:
            f.write("")

def log_result(result):
    with file_lock:
        with open(LOG_FILE, 'a') as f:
            f.write(f"{result}\n")

def calcular_media_movel(data, period):
    return pd.Series(data).rolling(window=period).mean().iloc[-1]

def calcular_heikin_ashi(klines):
    ha_open = [float(k[1]) for k in klines]
    ha_high = [float(k[2]) for k in klines]
    ha_low = [float(k[3]) for k in klines]
    ha_close = [float(k[4]) for k in klines]

    for i in range(1, len(klines)):
        ha_close[i] = (ha_open[i] + ha_high[i] + ha_low[i] + ha_close[i]) / 4
        ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2 if i > 1 else (klines[0][1] + klines[0][4]) / 2
        ha_high[i] = max(ha_high[i], ha_open[i], ha_close[i])
        ha_low[i] = min(ha_low[i], ha_open[i], ha_close[i])

    return ha_open, ha_close


def calcula_alvo(preco_entrada, tipo):
    lucro = preco_entrada * PROFIT_PERC
    return preco_entrada + lucro if tipo == 'long' else preco_entrada - lucro

def calcula_stop(preco_entrada, tipo):
    perda = preco_entrada * LOSS_PERC
    return preco_entrada - perda if tipo == 'long' else preco_entrada + perda

def calcular_resultado(preco_entrada, preco_saida, direcao, quantidade):
    if direcao == 'long':
        pnl = (preco_saida - preco_entrada) * quantidade
    elif direcao == 'short':
        pnl = (preco_entrada - preco_saida) * quantidade
    return round(pnl, 2)  # retorna em USDT

def calcular_resumo_operacoes(operacoes):
    total_gains = sum(1 for op in operacoes if op[6].strip().upper() == 'GAIN')
    total_losses = sum(1 for op in operacoes if op[6].strip().upper() == 'LOSS')
    total_trades = total_gains + total_losses
    profit_total = sum(float(op[8]) for op in operacoes if op[6].strip().upper() in ['GAIN', 'LOSS'])
    taxa_acerto = round((total_gains / total_trades) * 100, 2) if total_trades > 0 else 0.0

    return total_gains, total_losses, round(profit_total, 2), taxa_acerto

def obter_preco_atual():
    try:
        return float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
    except Exception as e:
        logging.error(f"Erro ao obter preço atual: {e}")
        return 0.0

def contar_perdas_consecutivas(operacoes):
    perdas_consecutivas = 0
    for op in reversed(operacoes):
        if op[6].strip().upper() == 'LOSS':
            perdas_consecutivas += 1
        else:
            break
    return perdas_consecutivas

def decimal_places(num):
    s = f'{num:.8f}'.rstrip('0')
    if '.' in s:
        return len(s.split('.')[1])
    else:
        return 0

def ajustar_quantidade(qtd, step):
    casas = decimal_places(step)
    qtd_arredondada = math.floor(qtd / step) * step
    if qtd_arredondada < step:
        qtd_arredondada = step
    return round(qtd_arredondada, casas)

def abrir_posicao(tipo, tamanho):
    try:
        symbol_info = client.get_symbol_info(SYMBOL)
        step_size = 0.0
        for filtro in symbol_info['filters']:
            if filtro['filterType'] == 'LOT_SIZE':
                step_size = float(filtro['stepSize'])
                break

        tamanho = ajustar_quantidade(tamanho, step_size)
        lado = 'BUY' if tipo == 'long' else 'SELL'
        print(f"Enviando ordem: lado={lado}, quantidade={tamanho}")
        
        order = client.futures_create_order(
            symbol=SYMBOL,
            side=lado,
            type='MARKET',
            quantity=tamanho
        )
        
        print(f"Ordem executada: {order}")
        return order
    except BinanceAPIException as e:
        print(f"Erro ao abrir posição: {e}")
        return None
    
def fechar_posicao(qtd, tipo):
    try:
        lado = 'SELL' if tipo == 'long' else 'BUY'
        client.futures_create_order(symbol=SYMBOL, side=lado, type='MARKET', quantity=abs(qtd))
        preco_fechamento = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])

        with status_lock:
            preco_abertura = status_bot.get("posicao", 0)
            quantidade = status_bot.get("quantidade", 0)
            direcao = status_bot.get("direcao", "---")

        # Calcular ROI considerando tipo LONG ou SHORT
        if direcao.lower() == "long":
            roi = round(((preco_fechamento - preco_abertura) / preco_abertura) * 100, 2)
        else:  # short
            roi = round(((preco_abertura - preco_fechamento) / preco_abertura) * 100, 2)

        # Calcular lucro em USDT
        lucro_usdt = round((preco_fechamento - preco_abertura) * quantidade, 2) if direcao.lower() == 'long' else round((preco_abertura - preco_fechamento) * quantidade, 2)

        resultado = "GAIN" if lucro_usdt >= 0 else "LOSS"

        # Salvar no banco de dados com 8 parâmetros
        salvar_operacao(
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            preco_abertura,
            preco_fechamento,
            direcao,
            quantidade,
            resultado,
            roi,
            lucro_usdt
        )

        logging.info(f"Fechamento de posição {tipo.upper()} | Quantidade: {qtd} | ROI: {roi:.2f}% | Lucro: {lucro_usdt} USDT")
        print(f"Fechamento de posição {tipo.upper()} | Quantidade: {qtd} | ROI: {roi:.2f}% | Lucro: {lucro_usdt} USDT")
        # Resetar status_bot corretamente
        with status_lock:
            status_bot["direcao"] = "---"
            status_bot["posicao"] = "---"
            status_bot["quantidade"] = 0
            status_bot["preco_saida"] = preco_fechamento
            status_bot["preco_atual"] = obter_preco_atual()  # atualiza preço atual
            status_bot["lucro_usdt"] = lucro_usdt
            

    except BinanceAPIException as e:
        logging.error(f"Erro ao fechar posição: {e}")

def obter_posicao():
    try:
        preco_agora =  float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
        with status_lock:
            status_bot["preco_atual"] = preco_agora            
    except BinanceAPIException as er:
        logging.error(f"Erro ao atualizar preco atual: {e}")
    try:
        for pos in client.futures_position_information(symbol=SYMBOL):
            if float(pos['positionAmt']) != 0:               
                return pos
        return None
    except BinanceAPIException as e:
        logging.error(f"Erro ao obter posição: {e}")
        return None

def monitorar_posicao(posicao):
    qtd = float(posicao['positionAmt'])
    tipo = 'long' if qtd > 0 else 'short'
    preco_entrada = float(posicao['entryPrice'])

    lucro = preco_entrada * PROFIT_PERC
    perda = preco_entrada * LOSS_PERC
    alvo = preco_entrada + lucro if tipo == 'long' else preco_entrada - lucro
    stop = preco_entrada - perda if tipo == 'long' else preco_entrada + perda

    try:
        preco_atual = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
        with status_lock:
            status_bot.update({                
                "preco": preco_atual, 
                "posicao": preco_entrada, 
                "quantidade": abs(qtd),
                "direcao": tipo.upper()  # Manter direção atualizada
            })
            logging.info(f"Posição atual: {qtd}")
            logging.debug(f"monitorar_posicao: status_bot['direcao'] = {status_bot['direcao']}")

        if (tipo == 'long' and preco_atual >= alvo) or (tipo == 'short' and preco_atual <= alvo):
            fechar_posicao(qtd, tipo)
            clear_loss()
            log_result("GAIN")
        elif (tipo == 'long' and preco_atual <= stop) or (tipo == 'short' and preco_atual >= stop):
            fechar_posicao(qtd, tipo)
            write_loss()
            log_result("LOSS")
    except BinanceAPIException as e:
        logging.error(f"Erro ao monitorar posição: {e}")

def verificar_entrada():
    try:
        klines = client.futures_klines(symbol=SYMBOL, interval=INTERVAL, limit=610)
        klines = [[float(v) for v in k] for k in klines]

        ha_open, ha_close = calcular_heikin_ashi(klines)
        close_prices = [k[4] for k in klines]
        media = calcular_media_movel(close_prices, 610)
        
        # Confirmar sinal
        if (ha_close[-2] < ha_open[-2] and ha_close[-1] > ha_open[-1]):
            return 'long'
        elif (ha_close[-2] > ha_open[-2] and ha_close[-1] < ha_open[-1]):
            return 'short'
        return None
    except BinanceAPIException as e:
        logging.error(f"Erro ao verificar entrada: {e}")
        return None
        
def executar_bot():
    init_loss_file()
    init_db()
    while True:
        try:
            perdas = read_loss_count()
            if perdas >= EMERGENCY_STOP_LOSSES:
                logging.warning("Parada de emergência: muitas perdas consecutivas.")
                time.sleep(3600)  # Pausa de 1 hora
                clear_loss()
                continue

            posicao = obter_posicao()
            if posicao:
                monitorar_posicao(posicao)
                time.sleep(1)
            else:
                if perdas >= MAX_GALE:
                    logging.warning("Limite de gales atingido. Pausando entradas.")
                    time.sleep(300)  # Pausa de 5 minutos
                    clear_loss()
                    continue
                
                direcao = verificar_entrada()
                
                if direcao:
                    idx = int(min(perdas, len(GALE)-1))
                    tamanho = GALE[idx]
                    print(f"Idx: {idx} tamanho {tamanho} direção: {direcao}")
                    abrir_posicao(direcao, tamanho)
                time.sleep(5)  # Intervalo maior sem posições
        except Exception as e:
            logging.error(f"Erro inesperado: {e}")
            time.sleep(10)

# ================= MAIN ================= #
if __name__ == '__main__':
    bot_thread = threading.Thread(target=executar_bot)
    bot_thread.daemon = True
    bot_thread.start()
    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)
