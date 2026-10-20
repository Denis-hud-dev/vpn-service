(() => {
  'use strict';
  const instructions = {
    android: 'Скопируйте личную ссылку, откройте Happ на Android и импортируйте подписку из буфера обмена.',
    ios: 'Скопируйте личную ссылку, откройте Happ на iPhone и добавьте подписку из буфера обмена.',
    windows: 'Скопируйте личную ссылку, откройте Happ на Windows и импортируйте подписку из буфера обмена.',
    macos: 'Скопируйте личную ссылку, откройте Happ на Mac и импортируйте подписку из буфера обмена.'
  };
  const button = document.getElementById('copy-primary');
  const feedback = document.getElementById('copy-feedback');
  const link = button.dataset.subscription;
  async function copy() {
    try {
      await navigator.clipboard.writeText(link);
      feedback.textContent = 'Ссылка скопирована. Откройте Happ и выберите импорт подписки из буфера обмена.';
      button.classList.add('copied');
    } catch (_) {
      document.getElementById('private-link').hidden = false;
      feedback.textContent = 'Автоматическое копирование недоступно. Выделите ссылку ниже или используйте QR-код.';
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
    document.getElementById('platform-instruction').textContent = instructions[tab.dataset.platform];
  }));
})();
