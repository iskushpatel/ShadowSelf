# Run this once to patch services.py with the missing upload functions
# python services_patch.py

import re

with open("services.py", "r", encoding="utf-8") as f:
    content = f.read()

FACEBOOK_FN = '''

async def ingest_facebook_upload(
    db,
    *,
    user_id,
    email,
    username,
    zip_bytes: bytes,
):
    import tempfile, os
    from backend.ingestion.facebook_zip_parser import FacebookZipParser
    user = await resolve_user(db, user_id=user_id, email=email, username=username)
    tf = None
    try:
        tf = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        tf.write(zip_bytes); tf.flush(); tf.close()
        parser = FacebookZipParser()
        counts = await _persist_posts(db, user_id=user.id, source="meta", posts=parser.parse(tf.name, user.id))
    finally:
        if tf:
            try: os.unlink(tf.name)
            except Exception: pass
    return user, *counts


async def ingest_instagram_upload(
    db,
    *,
    user_id,
    email,
    username,
    zip_bytes: bytes,
):
    import tempfile, os
    from backend.ingestion.instagram_zip_parser import InstagramZipParser
    user = await resolve_user(db, user_id=user_id, email=email, username=username)
    tf = None
    try:
        tf = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        tf.write(zip_bytes); tf.flush(); tf.close()
        parser = InstagramZipParser()
        counts = await _persist_posts(db, user_id=user.id, source="meta", posts=parser.parse(tf.name, user.id))
    finally:
        if tf:
            try: os.unlink(tf.name)
            except Exception: pass
    return user, *counts
'''

if "ingest_facebook_upload" not in content:
    content = content.rstrip() + FACEBOOK_FN + "\n"
    with open("services.py", "w", encoding="utf-8") as f:
        f.write(content)
    print("Patched successfully.")
else:
    print("Already patched — nothing to do.")
