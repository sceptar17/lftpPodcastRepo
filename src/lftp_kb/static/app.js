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
});
