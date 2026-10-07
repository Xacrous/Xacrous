const form = document.getElementById('login');
const err = document.getElementById('error');

fetch('/api/login-info').then(r => r.json()).then(info => {
  if (info.totp) {
    document.getElementById('code-row').classList.remove('hidden');
    form.code.required = true;
  }
});

form.onsubmit = async (e) => {
  e.preventDefault();
  err.textContent = '';
  const btn = form.querySelector('button');
  btn.disabled = true;
  try {
    const r = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'btcbot' },
      body: JSON.stringify({ username: form.username.value, password: form.password.value, code: form.code.value }),
    });
    if (r.ok) { location.href = '/'; return; }
    err.textContent = (await r.json()).detail || 'Sign in failed';
    form.password.value = ''; form.code.value = '';
  } catch (_) {
    err.textContent = 'Could not reach the server';
  } finally {
    btn.disabled = false;
  }
};
