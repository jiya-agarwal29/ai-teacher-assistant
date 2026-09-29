
function AnimatedBackground() {
  return (
    <div className="fixed inset-0 overflow-hidden -z-10 pointer-events-none theme-transition">
      {/* Soft ink-pine wash, top left */}
      <div className="absolute top-[-320px] left-[-280px] w-[680px] h-[680px] rounded-full blur-[150px] opacity-[0.07] dark:opacity-[0.12] bg-violet-500"></div>

      {/* Soft brass wash, bottom right */}
      <div className="absolute bottom-[-320px] right-[-260px] w-[620px] h-[620px] rounded-full blur-[150px] opacity-[0.06] dark:opacity-[0.10] bg-indigo-400"></div>

      {/* Fine architectural grid, theme aware */}
      <div
        className="absolute inset-0 opacity-[0.035] dark:opacity-[0.05]"
        style={{
          backgroundImage: `
            linear-gradient(to right, currentColor 1px, transparent 1px),
            linear-gradient(to bottom, currentColor 1px, transparent 1px)
          `,
          backgroundSize: "64px 64px"
        }}
      ></div>
    </div>
  );
}

export default AnimatedBackground;
