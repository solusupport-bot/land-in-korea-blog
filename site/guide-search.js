(() => {
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const revealItems = [...document.querySelectorAll('.travel-home .interest-story, .travel-home .prep-link, .travel-home .section-heading')];
  if (!reduceMotion.matches && 'IntersectionObserver' in window) {
    try {
      const observer = new IntersectionObserver(entries => entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-visible');
          entry.target.classList.remove('motion-pending');
          observer.unobserve(entry.target);
        }
      }), {threshold: 0.12});
      revealItems.forEach((item, index) => {
        item.style.setProperty('--reveal-delay', ((index % 3) * 70) + 'ms');
        if (item.getBoundingClientRect().top > window.innerHeight) item.classList.add('motion-pending');
        observer.observe(item);
      });
      const showAll = () => revealItems.forEach(item => item.classList.remove('motion-pending'));
      reduceMotion.addEventListener('change', showAll);
      window.addEventListener('beforeprint', showAll);
    } catch (_) { revealItems.forEach(item => item.classList.remove('motion-pending')); }
  }
  document.querySelectorAll('[data-island-note]').forEach(marker => marker.addEventListener('click', event => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const note = document.getElementById('dokdo-note');
    if (note) {
      event.preventDefault(); note.open = !note.open;
      marker.classList.remove('is-activating');
      window.requestAnimationFrame(() => marker.classList.add('is-activating'));
    }
  }));
  const mobileNav = document.querySelector('details.mobile-nav');
  if (mobileNav) {
    mobileNav.querySelectorAll('a').forEach(link => link.addEventListener('click', () => { mobileNav.open = false; }));
    mobileNav.addEventListener('keydown', event => {
      if (event.key === 'Escape') { mobileNav.open = false; mobileNav.querySelector('summary').focus(); }
    });
  }
  const explorer = document.querySelector('.destination-explorer');
  if (explorer) {
    const cards = [...explorer.querySelectorAll('[data-destination-card]')];
    const pins = [...explorer.querySelectorAll('[data-map-region]')];
    const buttons = [...explorer.querySelectorAll('[data-preview-region]')];
    const select = region => {
      if (!cards.some(card => card.dataset.destinationCard === region)) return;
      cards.forEach(card => { card.hidden = card.dataset.destinationCard !== region; });
      pins.forEach(pin => { pin.classList.toggle('is-selected', pin.dataset.mapRegion === region); });
      buttons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.previewRegion === region)));
    };
    const activate = region => {
      select(region);
      pins.forEach(pin => pin.classList.remove('is-activating'));
      const pin = pins.find(pin => pin.dataset.mapRegion === region);
      if (pin) window.requestAnimationFrame(() => pin.classList.add('is-activating'));
    };
    buttons.forEach(button => button.addEventListener('click', () => {
      activate(button.dataset.previewRegion);
    }));
    pins.forEach(pin => {
      pin.setAttribute('aria-label', 'Preview ' + pin.dataset.mapRegion[0].toUpperCase() + pin.dataset.mapRegion.slice(1));
      pin.addEventListener('click', event => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        activate(pin.dataset.mapRegion);
      });
    });
    explorer.querySelector('.destination-choices').hidden = false;
    explorer.classList.add('is-enhanced');
    select('busan');
  }
  const regionExplorer = document.querySelector('.region-explorer');
  if (regionExplorer) {
    const cards = [...regionExplorer.querySelectorAll('[data-preview-card]')];
    const districts = [...regionExplorer.querySelectorAll('[data-map-region]')];
    const buttons = [...regionExplorer.querySelectorAll('[data-preview-region]')];
    const select = region => {
      if (!cards.some(card => card.dataset.previewCard === region)) return;
      cards.forEach(card => { card.hidden = card.dataset.previewCard !== region; });
      districts.forEach(d => { d.classList.toggle('is-selected', d.dataset.mapRegion === region); });
      buttons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.previewRegion === region)));
    };
    buttons.forEach(button => button.addEventListener('click', () => select(button.dataset.previewRegion)));
    districts.forEach(d => d.addEventListener('click', () => select(d.dataset.mapRegion)));
    districts.forEach(d => d.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(d.dataset.mapRegion); }
    }));
    regionExplorer.querySelector('.region-preview-choices').hidden = false;
    if (districts.length) select(districts[0].dataset.mapRegion);
  }
  const homeForm = document.getElementById('home-guide-search-form');
  if (homeForm) homeForm.addEventListener('submit', event => {
    event.preventDefault();
    const term = document.getElementById('home-guide-search').value.trim();
    window.location.assign(homeForm.action + (term ? '#search=' + encodeURIComponent(term) : ''));
  });
  const input = document.getElementById('guide-search');
  if (!input) return;
  const controls = document.querySelector('[data-search-control]');
  const cards = [...document.querySelectorAll('[data-guide]')];
  const status = document.getElementById('search-status');
  const empty = document.getElementById('search-empty');
  const filter = () => {
    const words = input.value.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
    let count = 0;
    cards.forEach(card => {
      const text = card.dataset.search.toLocaleLowerCase();
      const match = words.every(word => text.includes(word));
      card.hidden = !match;
      if (match) count++;
    });
    status.textContent = `${count} of ${cards.length} guides`;
    empty.hidden = count !== 0;
  };
  input.addEventListener('input', filter);
  document.getElementById('clear-search').addEventListener('click', () => {
    input.value = ''; filter(); input.focus();
  });
  if (window.location.hash.startsWith('#search=')) {
    try { input.value = decodeURIComponent(window.location.hash.slice(8)); } catch (_) { input.value = ''; }
  }
  controls.hidden = false;
  filter();
})();