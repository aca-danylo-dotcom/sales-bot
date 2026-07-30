/* Прогрессивное улучшение форм CRM. Без этого файла страницы работают как
   раньше — весь смысл в том, чтобы убрать две ежедневные неприятности:

   1. Двойное нажатие «Сохранить». Пока браузер грузит страницу, кнопка живая,
      и второй тап отправляет форму ещё раз. На сервере от дублей фото стоит
      защита по хешу, но лишний запрос всё равно ни к чему.
   2. Повторный выбор фото в диалоге. Обычный <input multiple> заменяет весь
      набор: выбрал два файла, потом добавил третий — остался один третий.
      Здесь файлы накапливаются, а лишние можно убрать по одному. */
(function () {
  "use strict";

  /* --- Защита от повторной отправки --- */

  function submitButtons(form) {
    var own = form.querySelectorAll("button[type=submit], input[type=submit]");
    /* Кнопки карточки товара лежат вне формы и привязаны к ней атрибутом
       form="card" — querySelectorAll внутри формы их не видит. */
    var linked = form.id
      ? document.querySelectorAll('[form="' + form.id + '"]')
      : [];
    return Array.prototype.slice.call(own).concat(
      Array.prototype.filter.call(linked, function (el) {
        return el.type === "submit";
      })
    );
  }

  document.addEventListener("submit", function (event) {
    /* Слушаем на всплытии: inline-обработчик confirm() на самой форме
       срабатывает раньше, и отменённое им удаление сюда не доходит. */
    if (event.defaultPrevented) return;

    var form = event.target;
    if (form.dataset.sent === "1") {
      event.preventDefault();
      return;
    }
    form.dataset.sent = "1";

    /* Гасим кнопки следующим тиком: disabled в момент отправки стёр бы
       name/value нажатой кнопки, и «Готово» ушло бы как обычное сохранение. */
    var buttons = submitButtons(form);
    window.setTimeout(function () {
      buttons.forEach(function (button) {
        button.disabled = true;
        if (button.tagName === "BUTTON" && !button.dataset.label) {
          button.dataset.label = button.textContent;
          button.textContent = "Сохраняю…";
        }
      });
    }, 0);

    /* Возврат «назад» показывает страницу из кеша вместе с погашенными
       кнопками — на такой странице ничего не нажать. Возвращаем как было. */
    window.addEventListener("pageshow", function (revisit) {
      if (!revisit.persisted) return;
      delete form.dataset.sent;
      buttons.forEach(function (button) {
        button.disabled = false;
        if (button.dataset.label) {
          button.textContent = button.dataset.label;
          delete button.dataset.label;
        }
      });
    });
  });

  /* --- Накопление выбранных фото --- */

  function supportsDataTransfer() {
    try {
      return new DataTransfer().items !== undefined;
    } catch (error) {
      return false;
    }
  }

  function fileKey(file) {
    return file.name + "|" + file.size + "|" + file.lastModified;
  }

  function setupPicker(input) {
    var picked = [];

    var list = document.createElement("ul");
    list.className = "picked";
    input.parentNode.appendChild(list);

    function apply() {
      var box = new DataTransfer();
      picked.forEach(function (file) {
        box.items.add(file);
      });
      input.files = box.files;
      render();
    }

    function render() {
      list.textContent = "";
      picked.forEach(function (file, index) {
        var item = document.createElement("li");

        var name = document.createElement("span");
        name.textContent = file.name;
        item.appendChild(name);

        var drop = document.createElement("button");
        drop.type = "button";
        drop.className = "btn ghost small";
        drop.textContent = "Убрать";
        drop.addEventListener("click", function () {
          picked.splice(index, 1);
          apply();
        });
        item.appendChild(drop);

        list.appendChild(item);
      });
    }

    input.addEventListener("change", function () {
      Array.prototype.forEach.call(input.files, function (file) {
        var known = picked.some(function (have) {
          return fileKey(have) === fileKey(file);
        });
        if (!known) picked.push(file);
      });
      apply();
    });
  }

  if (supportsDataTransfer()) {
    var inputs = document.querySelectorAll('input[type=file][multiple]');
    Array.prototype.forEach.call(inputs, setupPicker);
  }

  /* --- Ещё строка размера на странице создания товара ---

     Строк было ровно три, и четвёртый размер приходилось дозаполнять уже в
     карточке — то есть заводить товар в два захода. Кнопка копирует последнюю
     строку и перенумеровывает поля; сервер читает столько строк, сколько пришло. */

  var rowsBox = document.getElementById("variant-rows");
  var addWrap = document.getElementById("add-variant-wrap");
  var addButton = document.getElementById("add-variant");

  if (rowsBox && addWrap && addButton) {
    var limit = parseInt(rowsBox.dataset.max, 10) || 30;
    addWrap.hidden = false;

    addButton.addEventListener("click", function () {
      var rows = rowsBox.querySelectorAll(".new-variant");
      if (!rows.length || rows.length >= limit) return;

      var index = rows.length;
      var row = rows[rows.length - 1].cloneNode(true);

      Array.prototype.forEach.call(row.querySelectorAll("input"), function (field) {
        /* Имя вида new_size_2 → new_size_3: номер в конце и есть номер строки. */
        field.name = field.name.replace(/\d+$/, index);
        field.value = "";
        var label = field.getAttribute("aria-label");
        if (label) field.setAttribute("aria-label", label.replace(/\d+$/, index + 1));
      });
      /* Подписи «Размер / Цвет / Остаток» стоят только над первой строкой —
         в копии их быть не должно, иначе они повторятся посреди таблицы. */
      Array.prototype.forEach.call(row.querySelectorAll("label > span"), function (caption) {
        caption.remove();
      });

      rowsBox.appendChild(row);
      var first = row.querySelector("input");
      if (first) first.focus();

      if (rows.length + 1 >= limit) addButton.disabled = true;
    });
  }
})();
