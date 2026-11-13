// _static/dynamic_urls.js
(function () {
  console.log("dynamic_urls.js loaded, hostname:", window.location.hostname);

  document.addEventListener("DOMContentLoaded", function () {
    const productionBase = "https://datatrove.as.arizona.edu";
    const currentBase = window.location.origin;

    console.log("currentBase:", currentBase);
    console.log("productionBase:", productionBase);

    const links = document.querySelectorAll("a");
    console.log("Total links found:", links.length);
    links.forEach(a => {
      console.log("Link href:", a.href, "| text:", a.textContent.trim());
    });

    if (currentBase === productionBase) {
      console.log("On production, skipping rewrite.");
      return;
    }

    document.querySelectorAll("a[href^='" + productionBase + "']").forEach(a => {
      console.log("Rewriting:", a.href);
      a.href = a.href.replace(productionBase, currentBase);
      if (a.textContent.includes(productionBase)) {
        a.textContent = a.textContent.replace(productionBase, currentBase);
      }
    });
  });
})();