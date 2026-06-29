import re

path = 'src/web/api_handlers.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

lines = content.split('\n')
new_lines = []
for line in lines:
    # Remove ENTER log
    if "handle_chat_sessions ENTER thread" in line:
        continue
    # Convert 'got client' info -> debug
    if "handle_chat_sessions got client" in line:
        new_lines.append(line.replace('logger.info(', 'logger.debug('))
        continue
    # Convert 'calling get_sessions' info -> debug
    if "calling get_sessions thread" in line:
        new_lines.append(line.replace('logger.info(', 'logger.debug('))
        continue
    new_lines.append(line)

with open(path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(new_lines))

# Clean oa_digest.py article loop logs (info->debug)
with open('src/assistant/oa_digest.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace(
    """        logger.info("[OA-DIGEST] Article '%s': scraped OK, content_len=%d, url=%s\"""",
    """        logger.debug("[OA-DIGEST] Article '%s': scraped OK, content_len=%d, url=%s\""""
)
content = content.replace(
    """        logger.info("[OA-DIGEST] Article '%s': using digest (url=%s)\"""",
    """        logger.debug("[OA-DIGEST] Article '%s': using digest (url=%s)\""""
)

with open('src/assistant/oa_digest.py', 'w', encoding='utf-8') as f:
    f.write(content)

# Comment out chat_records name resolution data dump
with open('src/web/api_handlers.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_line = 'logger.debug("chat_records name resolution: wxids=%s, names=%s, avatars_keys=%s"'
new_line = '# ' + old_line
content = content.replace(old_line, new_line)

with open('src/web/api_handlers.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('All done')
