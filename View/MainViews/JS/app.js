/* Small progressive enhancements. Every page works without JavaScript;
   this file only makes forms nicer. Loaded as an external file because the
   Content-Security-Policy forbids inline scripts. */
(function () {
  "use strict";

  function onReady(fn) {
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }

  onReady(function () {
    // Sort select submits its form on change.
    document.querySelectorAll(".js-autosubmit").forEach(function (el) {
      el.addEventListener("change", function () { el.form.submit(); });
    });

    // Quantity stepper on the product page.
    document.querySelectorAll(".qty").forEach(function (box) {
      var input = box.querySelector(".qty__input");
      if (!input) return;
      function step(delta) {
        var min = parseInt(input.min || "1", 10);
        var max = parseInt(input.max || "10", 10);
        var value = parseInt(input.value || "1", 10) + delta;
        input.value = String(Math.min(max, Math.max(min, isNaN(value) ? min : value)));
      }
      var dec = box.querySelector(".js-qty-dec");
      var inc = box.querySelector(".js-qty-inc");
      if (dec) dec.addEventListener("click", function () { step(-1); });
      if (inc) inc.addEventListener("click", function () { step(1); });
    });

    // "Color: Navy" label follows the selected swatch.
    var colorLabel = document.querySelector(".js-color-label");
    if (colorLabel) {
      document.querySelectorAll('input[name="color"]').forEach(function (radio) {
        radio.addEventListener("change", function () {
          colorLabel.textContent = radio.getAttribute("data-label") || radio.value;
        });
      });
    }

    // Sections shown only for some radio values (seller fields, card fields).
    document.querySelectorAll("[data-show-when]").forEach(function (section) {
      var form = section.closest("form");
      var group = section.getAttribute("data-radio");
      if (!form || !group) return;
      var wanted = section.getAttribute("data-show-when").split(" ");
      var radios = form.querySelectorAll('input[type="radio"][name="' + group + '"]');
      function sync() {
        var checked = form.querySelector('input[type="radio"][name="' + group + '"]:checked');
        section.hidden = !checked || wanted.indexOf(checked.value) === -1;
      }
      radios.forEach(function (r) { r.addEventListener("change", sync); });
      sync();
    });

    // Product form: dynamic attribute rows (flexible JSON in DynamoDB).
    var list = document.querySelector(".js-attr-list");
    var template = document.getElementById("attr-row-template");
    if (list && template) {
      function addRow(key) {
        var row = template.content.firstElementChild.cloneNode(true);
        list.appendChild(row);
        var inputs = row.querySelectorAll("input");
        if (key) { inputs[0].value = key; inputs[1].focus(); } else { inputs[0].focus(); }
      }
      list.addEventListener("click", function (event) {
        var button = event.target.closest(".js-attr-remove");
        if (button) button.closest(".attr-row").remove();
      });
      var add = document.querySelector(".js-attr-add");
      if (add) add.addEventListener("click", function () { addRow(""); });
      document.querySelectorAll(".js-attr-suggest").forEach(function (chip) {
        chip.addEventListener("click", function () {
          var key = chip.getAttribute("data-key");
          var exists = Array.prototype.some.call(list.querySelectorAll('input[name="attr_key"]'), function (i) {
            return i.value.trim().toLowerCase() === key.toLowerCase();
          });
          if (exists) return;
          var empty = Array.prototype.find.call(list.querySelectorAll(".attr-row"), function (row) {
            var inputs = row.querySelectorAll("input");
            return !inputs[0].value && !inputs[1].value;
          });
          if (empty) {
            var inputs = empty.querySelectorAll("input");
            inputs[0].value = key;
            inputs[1].focus();
          } else {
            addRow(key);
          }
        });
      });
    }

    // Invoice: print / save as PDF.
    document.querySelectorAll(".js-print").forEach(function (button) {
      button.addEventListener("click", function () { window.print(); });
    });
  });
})();
