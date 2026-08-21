import { globalIgnores } from 'eslint/config'
import { defineConfigWithVueTs, vueTsConfigs } from '@vue/eslint-config-typescript'
import pluginVue from 'eslint-plugin-vue'
import pluginOxlint from 'eslint-plugin-oxlint'
import skipFormatting from 'eslint-config-prettier/flat'

// To allow more languages other than `ts` in `.vue` files, uncomment the following lines:
// import { configureVueProject } from '@vue/eslint-config-typescript'
// configureVueProject({ scriptLangs: ['ts', 'tsx'] })
// More info at https://github.com/vuejs/eslint-config-typescript/#advanced-setup

export default defineConfigWithVueTs(
  {
    name: 'app/files-to-lint',
    files: ['**/*.{vue,ts,mts,tsx}'],
  },

  globalIgnores(['**/dist/**', '**/dist-ssr/**', '**/coverage/**']),

  ...pluginVue.configs['flat/essential'],
  vueTsConfigs.recommended,

  ...pluginOxlint.buildFromOxlintConfigFile('.oxlintrc.json'),

  // 项目惯例放行（均为刻意为之，非疏漏）：
  // 1. shadcn 设计体系的原语组件命名（Button/Card/... 单字，语义即文件名）
  {
    name: 'app/shadcn-ui-naming',
    files: ['src/components/ui/**/*.vue'],
    rules: {
      'vue/multi-word-component-names': 'off',
    },
  },
  // 2. `_class` 丢弃式解构（const { class: _class, ...rest } = props）：
  //    `_` 前缀即“故意不用”的既定惯例，仍需抓住其它真正未用的变量
  {
    name: 'app/underscore-ignored-vars',
    rules: {
      '@typescript-eslint/no-unused-vars': ['error', {
        varsIgnorePattern: '^_',
        argsIgnorePattern: '^_',
      }],
    },
  },
  // 3. 构建配置（vite.config.ts）里 Vite 中间件内部类型，务实用 any，不属业务代码
  {
    name: 'app/build-config',
    files: ['vite.config.ts'],
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
    },
  },

  skipFormatting,
)
