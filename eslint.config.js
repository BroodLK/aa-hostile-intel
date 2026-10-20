export default [
  {
    languageOptions: {
      globals: {
        window: "readonly",
        document: "readonly",
        $: "readonly",
        jQuery: "readonly",
        console: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
        setInterval: "readonly",
        clearInterval: "readonly",
        fetch: "readonly",
        bootstrap: "readonly",
      },
    },
    rules: {
      "no-unused-vars": "warn",
      "no-undef": "warn",
    },
  },
];
