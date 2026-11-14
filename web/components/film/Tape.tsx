// A strip of translucent paper tape holding a print to the page. Purely
// decorative; place it with absolute-position classes (top, left, rotate).

export function Tape(props: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={`pointer-events-none absolute z-10 block h-6 w-20 rounded-[1px] bg-[rgb(236_224_190/0.78)] shadow-[0_1px_2px_rgb(74_47_29/0.18)] mix-blend-multiply sm:h-7 sm:w-24 ${props.className ?? ""}`}
      style={{
        // Torn ends: a soft zigzag at both short edges.
        clipPath:
          "polygon(0 8%, 4% 0, 96% 4%, 100% 0, 98% 30%, 100% 55%, 97% 78%, 100% 100%, 4% 96%, 0 100%, 2% 70%, 0 45%, 3% 22%)",
      }}
    />
  );
}
