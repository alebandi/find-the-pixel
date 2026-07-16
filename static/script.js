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
const loginMessageEl = document.getElementById('login-message');
const loginErrorEl = document.getElementById('login-error');
const refLinkEl = document.getElementById('ref-link');
const btnCopy = document.getElementById('btn-copy');
const btnWhatsapp = document.getElementById('btn-whatsapp');
let gameOver = false;

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
  led.className = 'led ' + item.color + (item.owner ? ' owned' : ' free');
  if (item.owner) {
    led.dataset.owner = item.owner;
  }
  const containerId = LED_SLOT_CONTAINERS[i % LED_SLOT_CONTAINERS.length];
  document.getElementById(containerId).appendChild(led);
});

gridWrapper.addEventListener('mousemove', (e) => {
  if (e.target.classList.contains('led')) {
    const owner = e.target.dataset.owner;
    if (owner) {
      showTooltip('✨ Sponsor: @' + owner.replace(/^@/, ''), e.clientX, e.clientY);
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

// If the login redirect came back with an error (e.g. the anti-abuse
// security limit), reopen the modal and show the error in red inside it.
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

    // Lazy Login check FIRST: if login is required, show the modal and stop.
    if (data.require_login === true) {
      message.className = 'lose';
      showLoginModal(data.message);
      return;
    }

    // Only when require_login is false: referral link, then win/lose.
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
