const $ = (id) => document.getElementById(id);

const state = {
  objects: [],
  lastDirectObjectKey: "",
  lastDownloadUrl: "",
};

function setStatus(id, message, kind = "") {
  const el = $(id);
  el.textContent = message || "";
  el.className = `status ${kind}`.trim();
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: options.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) {
    throw new Error(data.message || data.detail || `HTTP ${response.status}`);
  }
  return data;
}

function configPayload() {
  return {
    access_key_id: $("access-key-id").value.trim(),
    secret_key: $("secret-key").value,
    bucket: $("bucket").value.trim(),
    prefix: $("prefix").value.trim(),
    endpoint: $("endpoint").value.trim(),
    region: $("region").value.trim(),
  };
}

function fillConfig(config) {
  $("access-key-id").value = config.access_key_id || "";
  $("secret-key").value = "";
  $("secret-key").placeholder = config.has_secret_key
    ? "Сохранённый ключ есть; оставьте пустым, чтобы не менять"
    : "Secret Key";
  $("bucket").value = config.bucket || "";
  $("prefix").value = config.prefix || "";
  $("endpoint").value = config.endpoint || "https://storage.yandexcloud.net";
  $("region").value = config.region || "ru-central1";
  $("files-prefix").value = config.prefix || "";
  $("upload-prefix").value = config.prefix || "";
  $("direct-prefix").value = config.prefix || "";
  $("config-path").textContent = config.config_path ? `Конфиг: ${config.config_path}` : "";
}

async function loadInitial() {
  try {
    const health = await api("/api/health");
    $("health").textContent = health.message;
    const config = await api("/api/config");
    fillConfig(config);
  } catch (error) {
    $("health").textContent = error.message;
  }
}

function switchTab(tabId) {
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === tabId));
  document.querySelectorAll(".panel").forEach((panel) => panel.classList.toggle("active", panel.id === tabId));
}

async function testConfig() {
  setStatus("connection-status", "Проверка подключения...");
  try {
    const data = await api("/api/config/test", { method: "POST", body: JSON.stringify(configPayload()) });
    setStatus("connection-status", data.message, "ok");
  } catch (error) {
    setStatus("connection-status", error.message, "err");
  }
}

async function applyConfig() {
  setStatus("connection-status", "Применение настроек...");
  try {
    const data = await api("/api/config/apply", { method: "POST", body: JSON.stringify(configPayload()) });
    setStatus("connection-status", data.message, "ok");
  } catch (error) {
    setStatus("connection-status", error.message, "err");
  }
}

async function saveConfig() {
  setStatus("connection-status", "Сохранение настроек...");
  try {
    const data = await api("/api/config/save", { method: "POST", body: JSON.stringify(configPayload()) });
    setStatus("connection-status", data.message, "ok");
    const config = await api("/api/config");
    fillConfig(config);
  } catch (error) {
    setStatus("connection-status", error.message, "err");
  }
}

async function clearConfig() {
  setStatus("connection-status", "Очистка настроек...");
  try {
    const data = await api("/api/config", { method: "DELETE" });
    setStatus("connection-status", data.message, "ok");
    fillConfig(await api("/api/config"));
  } catch (error) {
    setStatus("connection-status", error.message, "err");
  }
}

function formatSize(size) {
  if (!Number.isFinite(size)) return "";
  const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
  let value = size;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function sortedObjects() {
  const search = $("files-search").value.trim().toLowerCase();
  const sort = $("files-sort").value;
  const objects = state.objects
    .filter((object) => !search || object.key.toLowerCase().includes(search))
    .slice();

  const byDate = (a, b) => new Date(a.last_modified || 0) - new Date(b.last_modified || 0);
  const byName = (a, b) => a.key.localeCompare(b.key, "ru");
  const bySize = (a, b) => a.size - b.size;

  objects.sort((a, b) => {
    if (sort === "date-asc") return byDate(a, b);
    if (sort === "date-desc") return byDate(b, a);
    if (sort === "name-desc") return byName(b, a);
    if (sort === "size-desc") return bySize(b, a);
    if (sort === "size-asc") return bySize(a, b);
    return byName(a, b);
  });
  return objects;
}

function renderObjects() {
  const body = $("files-body");
  const objects = sortedObjects();
  body.innerHTML = "";
  if (objects.length === 0) {
    const row = document.createElement("tr");
    row.innerHTML = '<td colspan="6">Нет объектов для отображения</td>';
    body.appendChild(row);
    return;
  }
  for (const object of objects) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td class="key"></td>
      <td>${formatSize(object.size)}</td>
      <td>${object.last_modified ? new Date(object.last_modified).toLocaleString() : ""}</td>
      <td>${object.storage_class || ""}</td>
      <td>${object.etag || ""}</td>
      <td class="actions-cell">
        <div class="row-actions">
          <button class="secondary copy-key">Копировать key</button>
          <button class="secondary download-key">Download URL</button>
        </div>
      </td>`;
    row.querySelector(".key").textContent = object.key;
    row.querySelector(".copy-key").addEventListener("click", () => copyText(object.key));
    row.querySelector(".download-key").addEventListener("click", () => {
      $("download-object-key").value = object.key;
      switchTab("download-link");
    });
    body.appendChild(row);
  }
}

async function refreshFiles() {
  setStatus("files-status", "Загрузка списка объектов...");
  try {
    const prefix = encodeURIComponent($("files-prefix").value.trim());
    const data = await api(`/api/objects?prefix=${prefix}`);
    state.objects = data.objects || [];
    renderObjects();
    setStatus("files-status", `Объектов: ${state.objects.length}`, "ok");
  } catch (error) {
    setStatus("files-status", error.message, "err");
  }
}

async function generateUploadLink() {
  setStatus("upload-status", "Генерация ссылки...");
  $("upload-result").classList.add("hidden");
  try {
    const payload = {
      object_name: $("upload-name").value.trim(),
      prefix: $("upload-prefix").value.trim(),
      expires_in: Number($("upload-expires").value || 3600),
      content_type: $("upload-content-type").value.trim(),
      add_guid: $("upload-guid").checked,
      sanitize: $("upload-sanitize").checked,
      expected_file_type: $("upload-expected-type").value.trim(),
    };
    const data = await api("/api/objects/presign-upload", { method: "POST", body: JSON.stringify(payload) });
    $("upload-object-key").value = data.object_key;
    $("upload-client-url").value = data.client_url;
    $("upload-data-url").value = data.client_data_url;
    $("upload-url").value = data.upload_url;
    $("upload-result").classList.remove("hidden");
    setStatus("upload-status", `Ссылка действует до ${new Date(data.expires_at).toLocaleString()}`, "ok");
  } catch (error) {
    setStatus("upload-status", error.message, "err");
  }
}

async function generateDownloadLink() {
  setStatus("download-status", "Генерация ссылки...");
  $("download-result").classList.add("hidden");
  $("open-download").disabled = true;
  try {
    const payload = {
      object_key: $("download-object-key").value.trim(),
      expires_in: Number($("download-expires").value || 3600),
    };
    const data = await api("/api/objects/presign-download", { method: "POST", body: JSON.stringify(payload) });
    state.lastDownloadUrl = data.download_url;
    $("download-url").value = data.download_url;
    $("download-result").classList.remove("hidden");
    $("open-download").disabled = false;
    setStatus("download-status", `Ссылка действует до ${new Date(data.expires_at).toLocaleString()}`, "ok");
  } catch (error) {
    setStatus("download-status", error.message, "err");
  }
}

async function directUpload() {
  const file = $("direct-file").files[0];
  if (!file) {
    setStatus("direct-status", "Выберите файл", "err");
    return;
  }
  setStatus("direct-status", "Загрузка файла...");
  $("direct-result").classList.add("hidden");
  $("direct-show-files").disabled = true;
  const formData = new FormData();
  formData.append("file", file);
  formData.append("prefix", $("direct-prefix").value.trim());
  formData.append("object_name", $("direct-object-name").value.trim());
  formData.append("add_guid", $("direct-guid").checked ? "true" : "false");
  formData.append("sanitize", $("direct-sanitize").checked ? "true" : "false");
  try {
    const data = await api("/api/objects/upload-direct", { method: "POST", body: formData });
    state.lastDirectObjectKey = data.object_key;
    $("direct-object-key").value = data.object_key;
    $("direct-result").classList.remove("hidden");
    $("direct-show-files").disabled = false;
    setStatus("direct-status", `Файл загружен, размер ${formatSize(data.size)}`, "ok");
  } catch (error) {
    setStatus("direct-status", error.message, "err");
  }
}

async function copyText(text) {
  await navigator.clipboard.writeText(text);
}

async function copyFromElement(id) {
  const element = $(id);
  await copyText(element.value);
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => switchTab(tab.dataset.tab));
});

document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    const original = button.textContent;
    try {
      await copyFromElement(button.dataset.copy);
      button.textContent = "Скопировано";
      setTimeout(() => {
        button.textContent = original;
      }, 1200);
    } catch (error) {
      button.textContent = "Ошибка";
      setTimeout(() => {
        button.textContent = original;
      }, 1200);
    }
  });
});

$("test-config").addEventListener("click", testConfig);
$("apply-config").addEventListener("click", applyConfig);
$("save-config").addEventListener("click", saveConfig);
$("clear-config").addEventListener("click", clearConfig);
$("refresh-files").addEventListener("click", refreshFiles);
$("files-search").addEventListener("input", renderObjects);
$("files-sort").addEventListener("change", renderObjects);
$("generate-upload").addEventListener("click", generateUploadLink);
$("generate-download").addEventListener("click", generateDownloadLink);
$("open-download").addEventListener("click", () => {
  if (state.lastDownloadUrl) window.open(state.lastDownloadUrl, "_blank", "noopener");
});
$("direct-upload-button").addEventListener("click", directUpload);
$("direct-show-files").addEventListener("click", async () => {
  switchTab("files");
  await refreshFiles();
});

loadInitial();

