(() => {
  'use strict';
  const instructions = {
    android: ['На Android добавьте личную HTTPS-подписку в Happ из буфера обмена.', 'Откройте Happ, нажмите «+» и добавьте подписку из буфера обмена.'],
    ios: ['На iPhone добавьте личную HTTPS-подписку в Happ из буфера обмена.', 'Откройте Happ на iPhone, нажмите «+» и добавьте подписку из буфера обмена.'],
    windows: ['На Windows добавьте личную HTTPS-подписку в Happ из буфера обмена.', 'Откройте Happ на Windows и добавьте подписку из буфера обмена.'],
    macos: ['На Mac добавьте личную HTTPS-подписку в Happ из буфера обмена.', 'Откройте Happ на Mac и добавьте подписку из буфера обмена.']
  };
  const button = document.getElementById('copy-primary');
  const feedback = document.getElementById('copy-feedback');
  const link = button.dataset.subscription;
  function legacyCopy(value) {
    const field = document.createElement('textarea');
    field.value = value;
    field.setAttribute('readonly', '');
    field.style.position = 'fixed';
    field.style.opacity = '0';
    document.body.appendChild(field);
    field.select();
    try { return document.execCommand('copy'); }
    finally { field.remove(); }
  }
  async function copy() {
    let copied = false;
    try {
      await navigator.clipboard.writeText(link);
      copied = true;
    } catch (_) { /* Some Telegram webviews deny the modern clipboard API. */ }
    if (!copied) {
      try { copied = legacyCopy(link); } catch (_) { copied = false; }
    }
    if (copied) {
      feedback.textContent = 'Ссылка скопирована. Откройте Happ и добавьте подписку из буфера обмена.';
      button.classList.add('copied');
    } else {
      document.getElementById('private-link').hidden = false;
      feedback.textContent = 'Буфер обмена недоступен. Выделите личную ссылку ниже или используйте QR-код на другом устройстве.';
    }
  }
  button.addEventListener('click', copy);
  document.getElementById('copy-backup').addEventListener('click', copy);
  document.getElementById('show-link').addEventListener('click', () => {
    const panel = document.getElementById('private-link');
    panel.hidden = !panel.hidden;
  });
  document.getElementById('show-qr').addEventListener('click', () => {
    const panel = document.getElementById('qr-panel');
    panel.hidden = !panel.hidden;
  });
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  tabs.forEach(tab => tab.addEventListener('click', () => {
    tabs.forEach(other => other.setAttribute('aria-selected', String(other === tab)));
    const selected = instructions[tab.dataset.platform];
    document.getElementById('platform-instruction').textContent = selected[0];
    document.querySelector('#import-steps li:nth-child(2)').textContent = selected[1];
  }));
})();
