import sqlite3

def init_db():
    conn = sqlite3.connect('operacoes.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS operacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT,
            preco_abertura REAL,
            preco_fechamento REAL,
            direcao TEXT,
            quantidade REAL,
            resultado TEXT,
            roi REAL,
            lucro_usdt REAL
        )
    ''')
    conn.commit()
    conn.close()

def salvar_operacao(data, preco_abertura, preco_fechamento, direcao, quantidade, resultado, roi, lucro_usdt):
    conn = sqlite3.connect('operacoes.db')
    c = conn.cursor()
    c.execute('''
        INSERT INTO operacoes (data, preco_abertura, preco_fechamento, direcao, quantidade, resultado, roi, lucro_usdt)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (data, preco_abertura, preco_fechamento, direcao, quantidade, resultado, roi, lucro_usdt))
    conn.commit()
    conn.close()

def buscar_operacoes():
    conn = sqlite3.connect('operacoes.db')
    c = conn.cursor()
    c.execute("SELECT * FROM operacoes ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    return rows