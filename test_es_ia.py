import re

KW_IA = [
    'intel·ligència artificial', 'intel·ligencia artificial',
    r'\bia\b', r'\b(?:a\.i\.|ai)\b',
    'algoritme', 'algorisme', 'algorítmic',
    'automatitzaci', 'automatizaci',
    'machine learning', 'aprenentatge automàtic',
    'model de llenguatge', 'chatgpt', 'openai', 'llm',
    'dades personals', 'rgpd', 'reglament general de protecci',
    'plataforma digital', 'mercat digital', 'dsa', 'dma',
    'ciberseguretat', 'ciberseguridad',
    'regulació tecnol', 'regulacion tecnol',
    'reglament ia', 'eu ai act', 'ai act',
    'decisió automatitzada', 'decision automatizada',
    'blockchain', 'contracte intel·ligent',
    'deepfake', 'biometria'
]

def es_ia(titol: str, resum: str) -> bool:
    text = f"{titol} {resum}".lower()
    for kw in KW_IA:
        if kw.startswith(r'\b'):
            if re.search(kw, text, re.IGNORECASE):
                return True
        else:
            if kw in text:
                return True
    return False

print(es_ia("Llei d'IA", "bla bla"))
print(es_ia("Llei del foc", "res rellevant"))
