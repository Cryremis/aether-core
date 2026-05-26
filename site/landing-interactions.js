// AetherCore Landing Page Interactions

(function() {
  'use strict';

  // ================= Spotlight Effect =================
  function initSpotlight() {
    const container = document.getElementById('spotlight-container');
    if (!container) return;

    container.addEventListener('mousemove', (e) => {
      const target = e.target.closest('.spotlight-card');
      if (!(target instanceof HTMLElement)) return;

      const rect = target.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;

      target.style.setProperty('--mouse-x', `${x}px`);
      target.style.setProperty('--mouse-y', `${y}px`);
    });
  }

  // ================= Scroll Reveal =================
  function initScrollReveal() {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-revealed');
          observer.unobserve(entry.target);
        }
      });
    }, {
      threshold: 0.1,
      rootMargin: "0px 0px -50px 0px"
    });

    document.querySelectorAll('.reveal-on-scroll').forEach(el => observer.observe(el));
  }

  // ================= Hero Tilt Effect =================
  function initHeroTilt() {
    const wrapper = document.getElementById('hero-tilt-wrapper');
    if (!wrapper) return;

    // Check if device supports hover
    if (!window.matchMedia("(hover: hover) and (pointer: fine)").matches) return;

    const setTilt = (clientX, clientY) => {
      const rect = wrapper.getBoundingClientRect();
      const x = (clientX - rect.left) / rect.width - 0.5;
      const y = (clientY - rect.top) / rect.height - 0.5;

      wrapper.style.setProperty("--hero-tilt-x", `${(-y * 7).toFixed(2)}deg`);
      wrapper.style.setProperty("--hero-tilt-y", `${(x * 9).toFixed(2)}deg`);
      wrapper.style.setProperty("--hero-shift-x", `${(x * 18).toFixed(2)}px`);
      wrapper.style.setProperty("--hero-shift-y", `${(y * 14).toFixed(2)}px`);
      wrapper.style.setProperty("--hero-glint-x", `${((x + 0.5) * 100).toFixed(2)}%`);
      wrapper.style.setProperty("--hero-glint-y", `${((y + 0.5) * 100).toFixed(2)}%`);
    };

    const handlePointerMove = (event) => setTilt(event.clientX, event.clientY);
    const handlePointerLeave = () => {
      wrapper.style.setProperty("--hero-tilt-x", "4deg");
      wrapper.style.setProperty("--hero-tilt-y", "-2deg");
      wrapper.style.setProperty("--hero-shift-x", "0px");
      wrapper.style.setProperty("--hero-shift-y", "0px");
      wrapper.style.setProperty("--hero-glint-x", "50%");
      wrapper.style.setProperty("--hero-glint-y", "42%");
    };

    wrapper.addEventListener("pointermove", handlePointerMove);
    wrapper.addEventListener("pointerleave", handlePointerLeave);
  }

  // ================= Theme Toggle =================
  function initThemeToggle() {
    // Check saved theme or system preference
    const savedTheme = localStorage.getItem('theme');
    const systemTheme = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    const theme = savedTheme || systemTheme;

    document.documentElement.dataset.theme = theme;

    // Optional: Add theme toggle button
    // This is not included in the static page, but can be added if needed
  }

  // ================= Initialize =================
  function init() {
    initSpotlight();
    initScrollReveal();
    initHeroTilt();
    initThemeToggle();
  }

  // Run on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();