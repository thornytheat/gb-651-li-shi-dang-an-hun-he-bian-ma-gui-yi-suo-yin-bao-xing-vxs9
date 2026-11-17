const $ = (sel) => document.querySelector(sel);

async function postJSON(url, payload) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  const body = await res.json().catch(() => ({ error: "INVALID_RESPONSE" }));
  return { ok: res.ok, status: res.status, body };
}

function show(el, value) {
  el.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function refreshSoon() {
  setTimeout(() => location.reload(), 550);
}

$("#publish-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const out = $("#publish-result");
  form.querySelectorAll("button").forEach((b) => (b.disabled = true));
  try {
    const payload = {
      version: form.version.value.trim(),
      preset: form.preset.value,
      expected_revision: Number(form.expected_revision.value),
    };
    const { ok, body } = await postJSON("/api/fields/templates", payload);
    show(out, body);
    if (ok) refreshSoon();
  } finally {
    form.querySelectorAll("button").forEach((b) => (b.disabled = false));
  }
});

$("#receive-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const out = $("#receive-result");
  const file = form.file.files[0];
  if (!file) {
    show(out, "请选择接收文件");
    return;
  }
  const data = new FormData();
  data.append("file", file);
  if (form.batch_no.value.trim()) data.append("batch_no", form.batch_no.value.trim());
  if (form.template_version.value) data.append("template_version", form.template_version.value);
  if (form.max_records.value) data.append("max_records", form.max_records.value);
  form.querySelectorAll("button").forEach((b) => (b.disabled = true));
  out.textContent = "接收中";
  try {
    const res = await fetch("/api/fields/receive/upload", { method: "POST", body: data });
    const body = await res.json().catch(() => ({ error: "INVALID_RESPONSE" }));
    show(out, body);
    if (res.ok) refreshSoon();
  } catch (error) {
    show(out, "接收失败，可重试同一批号");
  } finally {
    form.querySelectorAll("button").forEach((b) => (b.disabled = false));
  }
});

document.querySelectorAll("[data-receipt]").forEach((btn) =>
  btn.addEventListener("click", async () => {
    const out = $("#batch-result");
    out.textContent = "读取回执";
    const res = await fetch(`/api/fields/batches/${encodeURIComponent(btn.dataset.receipt)}/receipt`);
    show(out, await res.json().catch(() => "读取失败"));
  })
);

document.querySelectorAll("[data-resume]").forEach((btn) =>
  btn.addEventListener("click", async () => {
    const out = $("#batch-result");
    out.textContent = "恢复接收";
    const { body } = await postJSON(`/api/fields/batches/${encodeURIComponent(btn.dataset.resume)}/resume`, {});
    show(out, body);
    refreshSoon();
  })
);
