from __future__ import annotations

import base64
import html
import json


def render_local_upload_page(token: str, expires_at: str) -> str:
    escaped_token = html.escape(token, quote=True)
    escaped_expires_at = html.escape(expires_at, quote=True)
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Загрузка файла</title>
  <style>
    :root {{ color-scheme: light; --border:#d7dde5; --text:#172033; --muted:#5f6b7a; --accent:#1463ff; --ok:#137b38; --err:#b42318; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; font-family: system-ui, -apple-system, Segoe UI, sans-serif; background: #f4f6f8; color: var(--text); display: grid; place-items: center; padding: 24px; }}
    main {{ width: min(520px, 100%); background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 24px; box-shadow: 0 12px 36px rgba(20, 30, 45, .08); }}
    h1 {{ margin: 0 0 8px; font-size: 24px; line-height: 1.2; letter-spacing: 0; }}
    p {{ margin: 0 0 18px; color: var(--muted); line-height: 1.45; }}
    label {{ display: block; font-size: 14px; margin-bottom: 8px; }}
    input[type=file] {{ width: 100%; border: 1px solid var(--border); border-radius: 6px; padding: 12px; background: #fff; }}
    button {{ margin-top: 16px; width: 100%; min-height: 44px; border: 0; border-radius: 6px; background: var(--accent); color: #fff; font-weight: 650; cursor: pointer; }}
    button:disabled {{ opacity: .55; cursor: wait; }}
    progress {{ width: 100%; height: 12px; margin-top: 14px; }}
    .status {{ min-height: 22px; margin-top: 14px; font-size: 14px; }}
    .ok {{ color: var(--ok); }}
    .err {{ color: var(--err); }}
    small {{ display: block; margin-top: 16px; color: var(--muted); }}
  </style>
</head>
<body>
  <main>
    <h1>Загрузка файла</h1>
    <p>Выберите файл и отправьте его. Другие файлы и настройки недоступны.</p>
    <form id="upload-form">
      <label for="file">Файл</label>
      <input id="file" name="file" type="file" required>
      <button id="submit" type="submit">Загрузить</button>
      <progress id="progress" value="0" max="100" hidden></progress>
      <div id="status" class="status"></div>
      <small>Ссылка действует до {escaped_expires_at}</small>
    </form>
  </main>
  <script>
    const form = document.getElementById('upload-form');
    const fileInput = document.getElementById('file');
    const submit = document.getElementById('submit');
    const statusBox = document.getElementById('status');
    const progress = document.getElementById('progress');
    form.addEventListener('submit', async (event) => {{
      event.preventDefault();
      const file = fileInput.files[0];
      if (!file) return;
      submit.disabled = true;
      progress.hidden = false;
      progress.value = 0;
      statusBox.className = 'status';
      statusBox.textContent = 'Передача файла в приложение...';
      const formData = new FormData();
      formData.append('file', file);
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/upload/{escaped_token}');
      xhr.upload.onprogress = (progressEvent) => {{
        if (!progressEvent.lengthComputable) return;
        const percent = Math.round((progressEvent.loaded / progressEvent.total) * 100);
        progress.value = percent;
        statusBox.textContent = 'Передача файла в приложение: ' + percent + '%';
      }};
      xhr.upload.onload = () => {{
        progress.removeAttribute('value');
        statusBox.textContent = 'Файл передан. Идёт загрузка в Object Storage...';
      }};
      xhr.onload = () => {{
        let data = {{}};
        try {{ data = JSON.parse(xhr.responseText || '{{}}'); }} catch (_) {{ data = {{ message: 'Неизвестный ответ сервера' }}; }}
        if (xhr.status < 200 || xhr.status >= 300 || data.ok === false) {{
          fail(new Error(data.message || data.detail || 'Ошибка загрузки'));
          return;
        }}
        progress.value = 100;
        statusBox.className = 'status ok';
        statusBox.textContent = 'Файл загружен.';
        fileInput.value = '';
        submit.disabled = false;
      }};
      xhr.onerror = () => fail(new Error('network write error during upload'));
      xhr.ontimeout = () => fail(new Error('timeout during upload'));
      xhr.send(formData);
      function fail(error) {{
        progress.hidden = true;
        statusBox.className = 'status err';
        statusBox.textContent = error.message;
        submit.disabled = false;
      }}
    }});
  </script>
</body>
</html>"""


def build_data_upload_url(upload_url: str, content_type: str = "", expected_file_type: str = "") -> str:
    payload = {
        "uploadUrl": upload_url,
        "contentType": content_type,
        "expectedFileType": expected_file_type,
    }
    html_page = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Загрузка файла</title>
  <style>
    :root {{ color-scheme: light; --border:#d7dde5; --text:#172033; --muted:#5f6b7a; --accent:#1463ff; --ok:#137b38; --err:#b42318; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; font-family: system-ui, -apple-system, Segoe UI, sans-serif; background: #f4f6f8; color: var(--text); display: grid; place-items: center; padding: 24px; }}
    main {{ width: min(520px, 100%); background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 24px; box-shadow: 0 12px 36px rgba(20, 30, 45, .08); }}
    h1 {{ margin: 0 0 8px; font-size: 24px; line-height: 1.2; letter-spacing: 0; }}
    p {{ margin: 0 0 18px; color: var(--muted); line-height: 1.45; }}
    input[type=file] {{ width: 100%; border: 1px solid var(--border); border-radius: 6px; padding: 12px; background: #fff; }}
    button {{ margin-top: 16px; width: 100%; min-height: 44px; border: 0; border-radius: 6px; background: var(--accent); color: #fff; font-weight: 650; cursor: pointer; }}
    button:disabled {{ opacity: .55; cursor: wait; }}
    .status {{ min-height: 22px; margin-top: 14px; font-size: 14px; }}
    .ok {{ color: var(--ok); }}
    .err {{ color: var(--err); }}
  </style>
</head>
<body>
  <main>
    <h1>Загрузка файла</h1>
    <p>Выберите файл и отправьте его. Другие файлы и настройки недоступны.</p>
    <form id="upload-form">
      <input id="file" type="file" required>
      <button id="submit" type="submit">Загрузить</button>
      <div id="status" class="status"></div>
    </form>
  </main>
  <script>
    const config = {json.dumps(payload, ensure_ascii=False)};
    const form = document.getElementById('upload-form');
    const fileInput = document.getElementById('file');
    const submit = document.getElementById('submit');
    const statusBox = document.getElementById('status');
    form.addEventListener('submit', async (event) => {{
      event.preventDefault();
      const file = fileInput.files[0];
      if (!file) return;
      if (config.expectedFileType && !matchesMime(file.type, config.expectedFileType)) {{
        statusBox.className = 'status err';
        statusBox.textContent = 'Выбран файл неподходящего типа.';
        return;
      }}
      submit.disabled = true;
      statusBox.className = 'status';
      statusBox.textContent = 'Загрузка...';
      const headers = {{}};
      if (config.contentType) headers['Content-Type'] = config.contentType;
      try {{
        const response = await fetch(config.uploadUrl, {{ method: 'PUT', body: file, headers }});
        if (!response.ok) throw new Error('Object Storage вернул HTTP ' + response.status);
        statusBox.className = 'status ok';
        statusBox.textContent = 'Файл загружен.';
        fileInput.value = '';
      }} catch (error) {{
        statusBox.className = 'status err';
        statusBox.textContent = error.message + '. Для прямой браузерной загрузки bucket должен разрешать CORS для PUT.';
      }} finally {{
        submit.disabled = false;
      }}
    }});
    function matchesMime(actual, expected) {{
      if (!expected) return true;
      if (!actual) return false;
      if (expected.endsWith('/*')) return actual.startsWith(expected.slice(0, -1));
      return actual === expected;
    }}
  </script>
</body>
</html>"""
    encoded = base64.b64encode(html_page.encode("utf-8")).decode("ascii")
    return f"data:text/html;charset=utf-8;base64,{encoded}"
