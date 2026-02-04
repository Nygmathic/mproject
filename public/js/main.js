/* =====================================================
   GLOBAL SITE SCRIPT
   - Mobile menu
   - Dark mode toggle (flash-free)
   ===================================================== */

document.addEventListener("DOMContentLoaded", () => {

  // ===== MOBILE MENU =====
  const menuBtn = document.getElementById("mobile-menu-btn");
  const navLinks = document.getElementById("nav-links");

  if (menuBtn && navLinks) {
    menuBtn.addEventListener("click", () => {
      const isOpen = navLinks.classList.toggle("active");
      menuBtn.textContent = isOpen ? "✕" : "☰ MENU";
    });
  }

  // ===== DARK MODE TOGGLE =====
  const themeToggle = document.getElementById("theme-toggle");
  const root = document.documentElement;

  if (themeToggle) {
    // Set initial button text
    themeToggle.textContent = root.classList.contains("dark-theme") ? "LIGHT MODE" : "DARK MODE";

    // Toggle dark mode on click
    themeToggle.addEventListener("click", () => {
      const isDark = root.classList.toggle("dark-theme");
      localStorage.setItem("theme", isDark ? "dark" : "light");
      themeToggle.textContent = isDark ? "LIGHT MODE" : "DARK MODE";
    });
  }

  // Re-enable transitions after full page load
  window.addEventListener("load", () => {
    root.classList.add("theme-loaded");
  });

});
