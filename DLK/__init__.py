```python
"""
DLK package

This package contains plugin modules for the DLK bot. Each plugin module
should import `bot` (the core module) and register handlers using the
`@bot.on_message` / `@bot.on_callback_query` decorators or via explicit
handler registration.

Example file layout:
DLK/
  __init__.py
  plugins/
    __init__.py
    play.py
    radio.py
    admin.py
"""
__all__ = ["plugins"]
