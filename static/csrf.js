/* Adds the CSRF token to every same-origin request that changes state, so no
   screen has to remember to. The token comes from <meta name="csrf-token">. */
(function () {
  var meta = document.querySelector('meta[name="csrf-token"]');
  if (!meta || !window.fetch) return;
  var token = meta.content;
  var original = window.fetch;
  window.fetch = function (input, init) {
    init = init || {};
    var method = (init.method || (input && input.method) || "GET").toUpperCase();
    if (method !== "GET" && method !== "HEAD" && method !== "OPTIONS") {
      var url = typeof input === "string" ? input : (input && input.url) || "";
      var sameOrigin = url.charAt(0) === "/" || url.indexOf(location.origin) === 0;
      if (sameOrigin) {
        var headers = new Headers(init.headers || (input && input.headers) || {});
        if (!headers.has("X-CSRFToken")) headers.set("X-CSRFToken", token);
        init.headers = headers;
      }
    }
    return original.call(this, input, init);
  };
})();
