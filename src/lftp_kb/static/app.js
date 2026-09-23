document.addEventListener("DOMContentLoaded", () => {
  const activeJob = document.querySelector("[data-auto-refresh='true']");
  if (activeJob) window.setTimeout(() => window.location.reload(), 5000);

  document.querySelectorAll("form[data-job-form='true']").forEach((form) => {
    form.addEventListener("submit", () => {
      form.querySelectorAll("button[type='submit'], button:not([type])").forEach((button) => {
        button.disabled = true;
        button.textContent = button.dataset.workingLabel || "Starting…";
      });
    });
  });

  document.querySelectorAll("[data-audio-jump]").forEach((button) => {
    button.addEventListener("click", () => {
      const audio = button.parentElement.querySelector("audio");
      if (!audio) return;
      const start = Number(button.dataset.audioJump || 0);
      const begin = () => {
        audio.currentTime = Math.min(start, Number.isFinite(audio.duration) ? audio.duration : start);
        audio.play();
      };
      if (audio.readyState >= 1) begin();
      else {
        audio.addEventListener("loadedmetadata", begin, { once: true });
        audio.load();
      }
    });
  });
});
