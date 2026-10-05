/*
 * Map Website - shared sidebar.
 *
 * The right-hand sidebar has one tab per feature. Each feature owns the panel
 * behind its tab and fills it from its own folder:
 *
 *     var panel = sidebar.panel('restaurants');   // a plain <section>
 *     panel.innerHTML = '<label>Price ...</label>';
 *
 *     sidebar.onShow('restaurants', function () { ... });  // tab opened
 *
 * Features never edit the sidebar markup in shared/index.html. Keep changes
 * here small, and tell the other person - both branches depend on this file.
 */
(function (global) {
  'use strict';

  function fail(message) {
    throw new Error('[MapWebsite] ' + message);
  }

  function createSidebar(options) {
    options = options || {};

    var root = document.getElementById(options.container || 'sidebar');
    if (!root) fail('No sidebar element "#' + (options.container || 'sidebar') + '".');

    var tabs = Array.prototype.slice.call(root.querySelectorAll('[data-panel]'));
    var listeners = {};  // name -> [fn]
    var active = null;

    function panelEl(name) {
      return document.getElementById('panel-' + name);
    }

    function show(name) {
      if (!panelEl(name)) fail('No sidebar panel "' + name + '".');
      if (name === active) return sidebar;
      active = name;

      tabs.forEach(function (tab) {
        var on = tab.getAttribute('data-panel') === name;
        tab.setAttribute('aria-selected', on ? 'true' : 'false');
        tab.tabIndex = on ? 0 : -1;
        panelEl(tab.getAttribute('data-panel')).hidden = !on;
      });

      (listeners[name] || []).forEach(function (fn) { fn(); });
      return sidebar;
    }

    tabs.forEach(function (tab, i) {
      tab.addEventListener('click', function () {
        show(tab.getAttribute('data-panel'));
      });
      // Arrow keys move between tabs, as screen-reader users expect.
      tab.addEventListener('keydown', function (e) {
        var step = e.key === 'ArrowDown' || e.key === 'ArrowRight' ? 1
                 : e.key === 'ArrowUp' || e.key === 'ArrowLeft' ? -1 : 0;
        if (!step) return;
        e.preventDefault();
        var next = tabs[(i + step + tabs.length) % tabs.length];
        next.focus();
        show(next.getAttribute('data-panel'));
      });
    });

    var sidebar = {
      /* The <section> a feature fills with its own controls. */
      panel: function (name) {
        var el = panelEl(name);
        if (!el) fail('No sidebar panel "' + name + '".');
        return el;
      },

      /* Open a feature's tab. */
      show: show,

      /* Run fn whenever a feature's tab is opened. */
      onShow: function (name, fn) {
        (listeners[name] = listeners[name] || []).push(fn);
        if (name === active) fn();
        return sidebar;
      },

      /* The name of the open tab, e.g. 'restaurants'. */
      active: function () {
        return active;
      }
    };

    show(options.initial || tabs[0].getAttribute('data-panel'));
    return sidebar;
  }

  global.MapWebsite = global.MapWebsite || {};
  global.MapWebsite.createSidebar = createSidebar;
})(window);
