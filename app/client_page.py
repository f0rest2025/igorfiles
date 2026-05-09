from __future__ import annotations

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
    :root {{ color-scheme: light; --border:#d5ded7; --text:#1d2a23; --muted:#66746b; --accent:#2f7d5b; --accent-dark:#276849; --ok:#18734a; --err:#b42318; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; font-family: system-ui, -apple-system, Segoe UI, sans-serif; background: #f2f5f1; color: var(--text); display: grid; place-items: center; padding: 24px; }}
    main {{ width: min(520px, 100%); background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 24px; box-shadow: 0 14px 36px rgba(31, 45, 36, .10); }}
    h1 {{ margin: 0 0 8px; font-size: 24px; line-height: 1.2; letter-spacing: 0; }}
    p {{ margin: 0 0 18px; color: var(--muted); line-height: 1.45; }}
    label {{ display: block; font-size: 14px; margin-bottom: 8px; }}
    input[type=file] {{ width: 100%; border: 1px solid var(--border); border-radius: 6px; padding: 12px; background: #fff; }}
    button {{ margin-top: 16px; width: 100%; min-height: 44px; border: 0; border-radius: 6px; background: var(--accent); color: #fff; font-weight: 650; cursor: pointer; }}
    button:hover {{ background: var(--accent-dark); }}
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
        const overallPercent = Math.min(45, Math.round(percent * 0.45));
        progress.value = overallPercent;
        statusBox.textContent = 'Загрузка: ' + overallPercent + '% (передача файла в приложение)';
      }};
      xhr.upload.onload = () => {{
        progress.removeAttribute('value');
        statusBox.textContent = 'Файл принят приложением. Идёт загрузка в Object Storage...';
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


def render_presigned_upload_page(upload_url: str, content_type: str = "", expected_file_type: str = "", expires_at: str = "") -> str:
    payload = {
        "uploadUrl": upload_url,
        "contentType": content_type,
        "expectedFileType": expected_file_type,
    }
    escaped_expires_at = html.escape(expires_at, quote=True)
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Загрузка файла</title>
  <style>
    :root {{ color-scheme: light; --border:#d5ded7; --text:#1d2a23; --muted:#66746b; --accent:#2f7d5b; --accent-dark:#276849; --ok:#18734a; --err:#b42318; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; font-family: system-ui, -apple-system, Segoe UI, sans-serif; background: #f2f5f1; color: var(--text); display: grid; place-items: center; padding: 24px; }}
    main {{ width: min(520px, 100%); background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 24px; box-shadow: 0 14px 36px rgba(31, 45, 36, .10); }}
    h1 {{ margin: 0 0 8px; font-size: 24px; line-height: 1.2; letter-spacing: 0; }}
    p {{ margin: 0 0 18px; color: var(--muted); line-height: 1.45; }}
    input[type=file] {{ width: 100%; border: 1px solid var(--border); border-radius: 6px; padding: 12px; background: #fff; }}
    button {{ margin-top: 16px; width: 100%; min-height: 44px; border: 0; border-radius: 6px; background: var(--accent); color: #fff; font-weight: 650; cursor: pointer; }}
    button:hover {{ background: var(--accent-dark); }}
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
      <input id="file" type="file" required>
      <button id="submit" type="submit">Загрузить</button>
      <progress id="progress" value="0" max="100" hidden></progress>
      <div id="status" class="status"></div>
      <small>Ссылка действует до {escaped_expires_at}</small>
    </form>
  </main>
  <script>
    const config = {json.dumps(payload, ensure_ascii=False)};
    const form = document.getElementById('upload-form');
    const fileInput = document.getElementById('file');
    const submit = document.getElementById('submit');
    const statusBox = document.getElementById('status');
    const progress = document.getElementById('progress');
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
      progress.hidden = false;
      progress.value = 0;
      statusBox.className = 'status';
      statusBox.textContent = 'Загрузка...';
      const headers = {{}};
      if (config.contentType) headers['Content-Type'] = config.contentType;
      const xhr = new XMLHttpRequest();
      xhr.open('PUT', config.uploadUrl);
      for (const [name, value] of Object.entries(headers)) xhr.setRequestHeader(name, value);
      xhr.upload.onprogress = (event) => {{
        if (!event.lengthComputable) return;
        const percent = Math.round((event.loaded / event.total) * 100);
        const visiblePercent = Math.min(95, percent);
        progress.value = visiblePercent;
        if (percent >= 100) {{
          statusBox.textContent = 'Файл передан. Object Storage завершает загрузку...';
        }} else {{
          statusBox.textContent = 'Загрузка: ' + visiblePercent + '%';
        }}
      }};
      xhr.onload = () => {{
        if (xhr.status < 200 || xhr.status >= 300) {{
          fail(new Error('Object Storage вернул HTTP ' + xhr.status));
          return;
        }}
        progress.value = 100;
        statusBox.className = 'status ok';
        statusBox.textContent = 'Файл загружен.';
        fileInput.value = '';
        submit.disabled = false;
      }};
      xhr.onerror = () => fail(new Error('NetworkError при прямой загрузке. Проверьте CORS bucket для origin ' + window.location.origin + ', метода PUT и заголовка Content-Type.'));
      xhr.ontimeout = () => fail(new Error('timeout during upload'));
      xhr.send(file);
      function fail(error) {{
        progress.hidden = true;
        statusBox.className = 'status err';
        statusBox.textContent = error.message;
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
