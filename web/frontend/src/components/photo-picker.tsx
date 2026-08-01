/**
 * Выбор фото товара: плитки с превью вместо строки «файл не выбран».
 *
 * Раньше здесь стояло системное поле выбора файла и список имён под ним.
 * Продавец снимает товар телефоном, имена у снимков вида IMG_20260731_154233,
 * и по такому списку невозможно понять, что именно выбрано и в каком порядке —
 * а порядок важен: первое фото становится главным. Теперь видно сами снимки.
 *
 * Плитка — компонент Attachment из реестра shadcn/ui, владелец прислал на него
 * ссылку. Сам он ничего не грузит и файлов не выбирает: это оформление одной
 * вложенной штуки — превью, название, подпись, кнопка. Выбор файлов и всё
 * остальное вокруг — наше.
 *
 * Один компонент на две страницы: фото добавляют и при создании товара, и в
 * карточке, и раньше эти два места жили одинаковой, но отдельной разметкой.
 *
 * Файлы уходят на сервер при сохранении формы, здесь они только копятся —
 * поэтому наружу отдаётся обычный список File, а не идентификаторы.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { ImagePlus, X } from "lucide-react";

import {
  Attachment,
  AttachmentActions,
  AttachmentAction,
  AttachmentContent,
  AttachmentDescription,
  AttachmentGroup,
  AttachmentMedia,
  AttachmentTitle,
  AttachmentTrigger,
} from "./ui/attachment";

type Props = {
  files: File[];
  onChange: (files: File[]) => void;
  /** Подпись под плитками: у создания товара и у карточки она разная. */
  hint: string;
};

/** «840 КБ», «1.2 МБ» — размер снимка подсказывает, не тяжёлый ли он. */
function weight(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
  return `${(bytes / 1024 / 1024).toFixed(1)} МБ`;
}

export function PhotoPicker({ files, onChange, hint }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  /* Ссылки на превью живут ровно столько, сколько живёт набор файлов: браузер
     держит снимок в памяти, пока ссылку не отозвали, а снимков с телефона
     легко набирается на сотню мегабайт. */
  const previews = useMemo(() => files.map((file) => URL.createObjectURL(file)), [files]);
  useEffect(
    () => () => previews.forEach((url) => URL.revokeObjectURL(url)),
    [previews],
  );

  const add = (chosen: FileList | null) => {
    const pictures = Array.from(chosen ?? []).filter((file) => file.type.startsWith("image/"));
    if (pictures.length) onChange([...files, ...pictures]);
    // Диалог должен добавлять снимки, а не заменять набор: товар снимают в
    // несколько заходов. Поле сбрасываем, иначе тот же файл второй раз не
    // выберется — браузер не считает это изменением.
    if (input.current) input.current.value = "";
  };

  return (
    <div
      className={`photo-picker ${dragging ? "over" : ""}`}
      onDragOver={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(event) => {
        // Уход на дочерний элемент — не уход с области.
        if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false);
      }}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        add(event.dataTransfer.files);
      }}
    >
      <AttachmentGroup>
        {files.map((file, index) => (
          <Attachment key={`${file.name}-${file.lastModified}-${index}`} orientation="vertical">
            <AttachmentMedia variant="image">
              <img src={previews[index]} alt="" />
            </AttachmentMedia>
            <AttachmentContent>
              <AttachmentTitle>{file.name}</AttachmentTitle>
              {/* У первого снимка вместо веса — его роль: главным становится
                  он, и это единственное, что здесь важно знать заранее. */}
              <AttachmentDescription>
                {index === 0 ? "главное фото" : weight(file.size)}
              </AttachmentDescription>
            </AttachmentContent>
            <AttachmentActions>
              <AttachmentAction
                type="button"
                aria-label={`Убрать ${file.name}`}
                onClick={() => onChange(files.filter((_, at) => at !== index))}
              >
                <X />
              </AttachmentAction>
            </AttachmentActions>
          </Attachment>
        ))}

        {/* Плитка «добавить» стоит последней, а не первой: набор снимков читают
            слева направо, и первым должно быть главное фото, а не кнопка. */}
        <Attachment state="idle" orientation="vertical" className="photo-add">
          <AttachmentMedia>
            <ImagePlus />
          </AttachmentMedia>
          <AttachmentContent>
            <AttachmentTitle>Добавить</AttachmentTitle>
            <AttachmentDescription>или перетащить</AttachmentDescription>
          </AttachmentContent>
          <AttachmentTrigger
            aria-label="Выбрать фото"
            onClick={() => input.current?.click()}
          />
        </Attachment>
      </AttachmentGroup>

      <p className="muted small">{hint}</p>

      {/* Настоящее поле спрятано: его открывает плитка. Из обхода с клавиатуры
          убрано — иначе на нём останавливались бы дважды. */}
      <input
        ref={input}
        type="file"
        accept="image/*"
        multiple
        hidden
        tabIndex={-1}
        onChange={(event) => add(event.target.files)}
      />
    </div>
  );
}
