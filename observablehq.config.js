// Observable Framework configuration.
//
// `root` is set to "site" because the framework defaults to "src", which is
// where the Python package lives. Leaving the default would make the framework
// try to build gpa/*.py as pages.

export default {
  root: "site",
  title: "Global Power Atlas",

  pages: [
    { name: "Forecasting", path: "/forecast" },
    { name: "Battery", path: "/battery" },
    { name: "Methodology", path: "/methodology" },
  ],

  theme: ["air", "near-midnight"],
  toc: true,
  sidebar: true,
  pager: true,
  typographicQuotes: true,

  header: "",
  footer: ({ path }) =>
    `Built from primary system-operator data. ` +
    `<a href="https://github.com/Pedrods20/global-power-atlas">Source and methodology on GitHub</a>.`,

  // The site is served from a project page, so assets resolve under the
  // repository name rather than the domain root.
  base: process.env.GPA_BASE_PATH ?? "/",

  search: true,
};
