with open('src/assistant/oa_digest.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Convert 'scraped OK' info->debug
content = content.replace(
    'logger.info(\n                            "[OA-DIGEST] Article',
    'logger.debug(\n                            "[OA-DIGEST] Article'
)

with open('src/assistant/oa_digest.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('OK')
