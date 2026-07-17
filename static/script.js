const GRID_SIZE = parseInt(document.body.dataset.gridSize, 10);
const REF_CODE = document.body.dataset.refCode;
const USER_EMAIL = document.body.dataset.userEmail;
const grid = document.getElementById('grid');
const gridWrapper = document.getElementById('grid-wrapper');
const tooltip = document.getElementById('tooltip');
const message = document.getElementById('message');
const clicksLeftEl = document.getElementById('clicks-left');
const userInfoEl = document.getElementById('user-info');
const overlayReferral = document.getElementById('overlay-referral');
const overlayLogin = document.getElementById('overlay-login');
const overlaySponsor = document.getElementById('overlay-sponsor');
const loginMessageEl = document.getElementById('login-message');
const loginErrorEl = document.getElementById('login-error');
const refLinkEl = document.getElementById('ref-link');
const btnCopy = document.getElementById('btn-copy');
const btnWhatsapp = document.getElementById('btn-whatsapp');
const sponsorForm = document.getElementById('sponsor-form');
const sponsorFormError = document.getElementById('sponsor-form-error');
const sponsorModalTitle = document.getElementById('sponsor-modal-title');
const sponsorModalDesc = document.getElementById('sponsor-modal-desc');
const sponsorFieldColor = document.getElementById('sponsor-field-color');
const sponsorTextLabel = document.getElementById('sponsor-text-label');
const sponsorColorInput = document.getElementById('sponsor-color');
const sponsorColorHex = document.getElementById('sponsor-color-hex');
let gameOver = false;
let pendingCheckout = null;

clicksLeftEl.textContent = document.body.dataset.clicksLeft;
grid.style.gridTemplateColumns = 'repeat(' + GRID_SIZE + ', 1fr)';
grid.style.gridTemplateRows = 'repeat(' + GRID_SIZE + ', 1fr)';

if (USER_EMAIL) {
  userInfoEl.innerHTML =
    '👤 Logged in as: ' + USER_EMAIL + ' (<a href="/logout">Logout</a>)';
}

function buildRefLink(code) {
  return window.location.origin + '/?ref=' + code;
}

function showTooltip(text, x, y) {
  tooltip.textContent = text;
  tooltip.style.display = 'block';
  tooltip.style.left = (x + 14) + 'px';
  tooltip.style.top = (y + 14) + 'px';
}

function hideTooltip() {
  tooltip.style.display = 'none';
}

function isHexColor(value) {
  return /^#[0-9A-Fa-f]{6}$/.test(value);
}

function applyLedStyle(led, item) {
  if (item.owner && isHexColor(item.color)) {
    led.style.backgroundColor = item.color;
    led.style.boxShadow = '0 0 8px ' + item.color;
  }
}

/* --- Sponsor LED frame (state comes from the database via data-leds) --- */
const LED_SLOT_CONTAINERS = [
  'led-row-top', 'led-row-top', 'led-row-top',
  'led-col-right', 'led-col-right',
  'led-row-bottom', 'led-row-bottom', 'led-row-bottom',
  'led-col-left', 'led-col-left'
];

const LEDS = JSON.parse(document.body.dataset.leds || '[]');

LEDS.forEach((item, i) => {
  const led = document.createElement('div');
  const isOwned = Boolean(item.owner);
  const useNamedColor = isOwned && !isHexColor(item.color);
  led.className = 'led' + (useNamedColor ? ' ' + item.color : '') + (isOwned ? ' owned' : ' free');
  led.dataset.slot = item.slot;
  if (item.owner) {
    led.dataset.owner = item.owner;
    led.dataset.tooltip = item.owner;
    if (item.link) led.dataset.link = item.link;
  }
  applyLedStyle(led, item);
  const containerId = LED_SLOT_CONTAINERS[i % LED_SLOT_CONTAINERS.length];
  document.getElementById(containerId).appendChild(led);
});

function openSponsorModal(type, slotId) {
  pendingCheckout = { type: type, slot_id: slotId };
  sponsorForm.reset();
  sponsorFormError.textContent = '';
  sponsorColorInput.value = '#fbbf24';
  sponsorColorHex.value = '#fbbf24';

  if (type === 'LED') {
    sponsorModalTitle.textContent = 'Sponsor LED — Slot #' + (slotId + 1);
    sponsorModalDesc.textContent = 'Customize your LED for 24 hours.';
    sponsorFieldColor.style.display = 'block';
    document.getElementById('sponsor-field-days').style.display = 'none';
    sponsorTextLabel.textContent = 'Tooltip text';
    document.getElementById('sponsor-text').placeholder = 'e.g. @your_tiktok';
  } else {
    sponsorModalTitle.textContent = 'PRO Sponsor Banner';
    sponsorModalDesc.textContent = 'Your brand under the Daily Enigma.';
    sponsorFieldColor.style.display = 'none';
    document.getElementById('sponsor-field-days').style.display = 'block';
    sponsorTextLabel.textContent = 'Banner text';
    document.getElementById('sponsor-text').placeholder = 'e.g. Follow us on TikTok!';
  }

  overlaySponsor.classList.add('visible');
}

function closeSponsorModal() {
  overlaySponsor.classList.remove('visible');
  pendingCheckout = null;
  sponsorFormError.textContent = '';
}

async function startCheckout(payload) {
  try {
    const response = await fetch('/create-checkout-session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await response.json();
    if (data.url) {
      window.location.href = data.url;
    } else {
      sponsorFormError.textContent = data.error || 'Unable to start checkout. Please try again.';
    }
  } catch (err) {
    sponsorFormError.textContent = 'Connection error. Please try again.';
  }
}

sponsorColorInput.addEventListener('input', () => {
  sponsorColorHex.value = sponsorColorInput.value;
});

sponsorColorHex.addEventListener('input', () => {
  if (isHexColor(sponsorColorHex.value)) {
    sponsorColorInput.value = sponsorColorHex.value;
  }
});

sponsorForm.addEventListener('submit', (e) => {
  e.preventDefault();
  if (!pendingCheckout) return;

  const customText = document.getElementById('sponsor-text').value.trim();
  const customLink = document.getElementById('sponsor-link').value.trim();
  if (!customText || !customLink) {
    sponsorFormError.textContent = 'Please fill in all required fields.';
    return;
  }
  if (!customLink.startsWith('http://') && !customLink.startsWith('https://')) {
    sponsorFormError.textContent = 'Destination URL must start with http:// or https://';
    return;
  }

  const payload = {
    type: pendingCheckout.type,
    custom_text: customText,
    custom_link: customLink
  };
  if (pendingCheckout.type === 'LED') {
    payload.slot_id = pendingCheckout.slot_id;
    payload.color = isHexColor(sponsorColorHex.value) ? sponsorColorHex.value : '#fbbf24';
  } else {
    const daysInput = document.getElementById('sponsor-days');
    payload.days = parseInt(daysInput.value, 10) || 1;
  }

  startCheckout(payload);
});

document.getElementById('btn-close-sponsor').addEventListener('click', closeSponsorModal);

gridWrapper.addEventListener('click', (e) => {
  const led = e.target.closest('.led');
  if (!led) return;

  if (led.classList.contains('free')) {
    openSponsorModal('LED', parseInt(led.dataset.slot, 10));
    return;
  }

  if (led.classList.contains('owned') && led.dataset.link) {
    window.open(led.dataset.link, '_blank', 'noopener');
  }
});

document.querySelectorAll('#btn-pro-sponsor').forEach((btn) => {
  btn.addEventListener('click', () => openSponsorModal('PRO'));
});

gridWrapper.addEventListener('mousemove', (e) => {
  if (e.target.classList.contains('led')) {
    const tooltipText = e.target.dataset.tooltip;
    if (tooltipText) {
      showTooltip('✨ Sponsor: ' + tooltipText, e.clientX, e.clientY);
    } else {
      showTooltip('Space Available - Buy now!', e.clientX, e.clientY);
    }
  } else if (e.target.classList.contains('pixel')) {
    showTooltip('X: ' + e.target.dataset.x + ', Y: ' + e.target.dataset.y, e.clientX, e.clientY);
  } else {
    hideTooltip();
  }
});

gridWrapper.addEventListener('mouseleave', hideTooltip);

/* --- Game grid (size driven by GRID_SIZE from Python) --- */
for (let y = 1; y <= GRID_SIZE; y++) {
  for (let x = 1; x <= GRID_SIZE; x++) {
    const pixel = document.createElement('div');
    pixel.className = 'pixel';
    pixel.dataset.x = x;
    pixel.dataset.y = y;
    grid.appendChild(pixel);
  }
}

/* --- Modals --- */
function showReferralPopup(refCode) {
  refLinkEl.textContent = buildRefLink(refCode || REF_CODE);
  overlayReferral.classList.add('visible');
}

function showLoginModal(text, errorText) {
  loginMessageEl.textContent = text;
  if (errorText) {
    loginErrorEl.textContent = '\u26a0\ufe0f ' + errorText;
    loginErrorEl.classList.add('visible');
  } else {
    loginErrorEl.textContent = '';
    loginErrorEl.classList.remove('visible');
  }
  overlayLogin.classList.add('visible');
}

const loginError = new URLSearchParams(window.location.search).get('login_error');
if (loginError) {
  showLoginModal('Sign in with Google to unlock referrals and claim prizes.', loginError);
}

document.getElementById('btn-close-referral').addEventListener('click', () => {
  overlayReferral.classList.remove('visible');
});

document.getElementById('btn-close-login').addEventListener('click', () => {
  overlayLogin.classList.remove('visible');
});

btnCopy.addEventListener('click', async () => {
  const link = refLinkEl.textContent;
  try {
    await navigator.clipboard.writeText(link);
    btnCopy.textContent = '✅ Copied!';
  } catch (err) {
    const range = document.createRange();
    range.selectNodeContents(refLinkEl);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    document.execCommand('copy');
    btnCopy.textContent = '✅ Copied!';
  }
});

btnWhatsapp.addEventListener('click', () => {
  const link = refLinkEl.textContent;
  const text = encodeURIComponent('Play Find the Winning Pixel! Use my link: ' + link);
  window.open('https://wa.me/?text=' + text, '_blank');
});

/* --- Pixel click --- */
grid.addEventListener('click', async (e) => {
  if (gameOver || !e.target.classList.contains('pixel')) return;
  const pixel = e.target;
  const x = parseInt(pixel.dataset.x, 10);
  const y = parseInt(pixel.dataset.y, 10);

  try {
    const response = await fetch('/check_pixel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ x: x, y: y })
    });
    const data = await response.json();

    if (typeof data.clicks_left === 'number') {
      clicksLeftEl.textContent = data.clicks_left;
    }

    message.textContent = data.message;

    if (data.require_login === true) {
      message.className = 'lose';
      showLoginModal(data.message);
      return;
    }

    if (data.allowed === false) {
      message.className = 'lose';
      showReferralPopup(data.ref_code);
      return;
    }

    if (data.win) {
      message.className = 'win';
      pixel.classList.add('winner');
      gameOver = true;
    } else {
      message.className = 'lose';
      pixel.classList.add('wrong');
    }
  } catch (err) {
    message.textContent = '⚠️ Connection error. Please try again.';
  }
});
