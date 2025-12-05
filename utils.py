import sys

def safe_log(message):
    """
    Removes emojis and special characters that crash Windows terminals.
    Only allows standard ASCII characters.
    """
    if not message: return ""
    try:
        return message.encode('ascii', 'ignore').decode('ascii')
    except:
        return "Unsafe text removed"