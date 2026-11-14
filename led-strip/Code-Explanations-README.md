# Code Explanations

Hier sind für mich neue Dinge erklärt:


## Microdot Installation

Um Microdot mit einer Extension zu installieren (siehe [Microdot ReadTheDocs - Installation](https://microdot.readthedocs.io/en/latest/intro.html)), reicht es, am Raspberry Pi Pico einen Ordner `microdot` anzulegen, und in diesen die Datein `__init__.py`, `microdot.py` und die jeweilige Extension-Datei (hier `utemplate.py`) zu legen.

Allerdings hat `utemplate.py` eine externe Abhängigkeit zu einem gleichnamigen package `utemplate`. Dieses ist als ganzer Ordner im Gitrepo enthalten. Dieser Ordner muss samt Inhalt in das Raspberry Pi Pico Filesystem kopoiert werden (an oberster Stelle)

## Unterschiede zwischen Flask und Microdot

Die mit `@app.route` dekorierten Handler-Funktionen sollen `asyc` sein:

```python
@app.route('/')
async def index(request):
    return _render_config_page()
```

Standardrückgabe ist `Content-Type: text/plain`. Daher muss in der sollte man ein tuple mit drei Werten zurückliefern:

```python
    return "<p>HTML-Seite</p>", 200, {"Content-Type": "text/html"}
```

## Arbeiten mit Templates

```python
from microdot.utemplate import Template

rendered = Template("config.html").render(wert1="value1", wert2=3, ...)
```

Die Template-Datei `config.html` muss in einem Ordner `templates` sein, und Platzhalter enthalten: `{{ wert1 }}` , `{{ wert2 }}` usw.

Der Rückgabe-Wert von `Template("template-datei.html").reder(...)` ist ein str mit dem gerenderten html-Text.

## Parallele Verarbeitung mit async

Auf der [Microdot-Read-the-docs-Seite](microdot.readthedocs.io/en/latest/users-guide/intro.html) ist kurz erklärt, wie man den Webserver parallel mit anderen asynchronen Tasks laufen lassen kann. <duck.ai> hat mir erklärt, wie das konkret aussehen kann:

> In der Dokumentation vom microdot steht, dass ich einen Webserver asnychron starten kann und noch andere Tasks ausführen kann. ... Ich möchte eine Funktion read_sensor() aufrufen: Jede Sekunde. Diese benötigt kaum Laufzeit, kehrt also quasi sofort zurück. Wie gehe ich vor?

```python 
import asyncio
from microdot import Microdot

app = Microdot()

def read_sensor():
    # Sensor auslesen und Wert verarbeiten
    print("Sensor gelesen")

async def sensor_loop():
    while True:
        read_sensor()
        await asyncio.sleep(1)

async def main():
    server = asyncio.create_task(app.start_server())
    sensor_task = asyncio.create_task(sensor_loop())

    await server

asyncio.run(main())
```

Ein Link zur Async-Doku von Mikropython wird auch geliefert

- [Micropython asyncio](https://docs.micropython.org/en/latest/library/asyncio.html)

