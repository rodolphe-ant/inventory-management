<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <template v-else>
      <div v-if="lastOrder" class="success-banner">
        <div>
          <strong>{{ t('restocking.orderPlaced', { orderNumber: lastOrder.order_number }) }}</strong>
          <p class="small">
            {{ t('restocking.orderPlacedDetail', {
              count: lastOrder.items.length,
              total: money(lastOrder.total_cost),
              date: formatDate(lastOrder.expected_delivery),
              days: lastOrder.lead_time_days
            }) }}
          </p>
        </div>
        <div class="banner-actions">
          <router-link to="/orders" class="btn-secondary">{{ t('restocking.viewOrders') }}</router-link>
          <button class="btn-secondary" @click="lastOrder = null">{{ t('common.close') }}</button>
        </div>
      </div>

      <div v-if="submitError" class="error">{{ submitError }}</div>

      <div class="card budget-card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.budget') }}</h3>
          <span class="budget-value">{{ money(budgetAmount) }}</span>
        </div>
        <div class="budget-controls">
          <!--
            Keyed on currency so the slider re-mounts when the locale switches. On an
            in-place patch v-model writes `value` before `max`/`step` are updated, so the
            browser clamps the new (e.g. yen) value to the old (dollar) max and the thumb
            ends up out of sync with budgetInput. A fresh mount applies value after bounds.
          -->
          <input
            :key="currentCurrency"
            type="range"
            class="budget-slider"
            :min="0"
            :max="sliderMax"
            :step="sliderStep"
            v-model.number="budgetInput"
            @input="onBudgetInput"
          >
          <input
            type="number"
            class="budget-input"
            :min="0"
            :max="sliderMax"
            :step="sliderStep"
            v-model.number="budgetInput"
          >
        </div>
        <p class="help-text">{{ t('restocking.budgetHelp') }}</p>
      </div>

      <div class="stats-grid">
        <div class="stat-card info">
          <div class="stat-label">{{ t('restocking.budget') }}</div>
          <div class="stat-value">{{ money(budgetAmount) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.allocated') }}</div>
          <div class="stat-value">{{ money(allocated) }}</div>
        </div>
        <div class="stat-card" :class="remaining < 0 ? 'danger' : 'success'">
          <div class="stat-label">{{ t('restocking.remaining') }}</div>
          <div class="stat-value">{{ money(remaining) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.itemsSelected') }}</div>
          <div class="stat-value">{{ selectedItems.length }} / {{ candidates.length }}</div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <div>
            <h3 class="card-title">{{ t('restocking.recommendations') }} ({{ candidates.length }})</h3>
            <p class="card-subtitle">{{ t('restocking.recommendationHint') }}</p>
          </div>
          <div class="header-actions">
            <span v-if="overrideCount" class="manual-hint">{{ t('restocking.manualChanges', { count: overrideCount }) }}</span>
            <button v-if="overrideCount" class="btn-secondary" @click="resetSelection">{{ t('restocking.resetSelection') }}</button>
            <button class="btn-primary" :disabled="!canSubmit" @click="placeOrder">
              {{ submitting ? t('restocking.placing') : t('restocking.placeOrder') }}
            </button>
          </div>
        </div>

        <p v-if="overBudget" class="warning-text">{{ t('restocking.overBudget') }}</p>
        <p v-else-if="candidates.length && !selectedItems.length" class="muted-text">{{ t('restocking.nothingSelected') }}</p>

        <div v-if="!candidates.length" class="empty-state">{{ t('restocking.noCandidates') }}</div>
        <div v-else class="table-container">
          <table class="restock-table">
            <thead>
              <tr>
                <th class="col-include">{{ t('restocking.table.include') }}</th>
                <th>{{ t('restocking.table.sku') }}</th>
                <th>{{ t('restocking.table.itemName') }}</th>
                <th>{{ t('restocking.table.warehouse') }}</th>
                <th>{{ t('restocking.table.trend') }}</th>
                <th class="num">{{ t('restocking.table.onHand') }}</th>
                <th class="num">{{ t('restocking.table.forecast') }}</th>
                <th class="num">{{ t('restocking.table.quantity') }}</th>
                <th class="num">{{ t('restocking.table.unitCost') }}</th>
                <th class="num">{{ t('restocking.table.lineCost') }}</th>
                <th class="num">{{ t('restocking.table.leadTime') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="c in candidates"
                :key="c.sku"
                :class="{ 'row-muted': !isSelected(c) }"
                @click="toggle(c)"
              >
                <td>
                  <input
                    type="checkbox"
                    :checked="isSelected(c)"
                    @click.stop
                    @change="toggle(c)"
                  >
                </td>
                <td><strong>{{ c.sku }}</strong></td>
                <td>{{ translateProductName(c.name) }}</td>
                <td>{{ translateWarehouse(c.warehouse) }}</td>
                <td>
                  <span :class="['badge', c.trend]">{{ t(`trends.${c.trend}`) }}</span>
                </td>
                <td class="num">{{ c.quantity_on_hand }}</td>
                <td class="num">{{ c.forecasted_demand }}</td>
                <td class="num"><strong>{{ c.recommended_quantity }}</strong></td>
                <td class="num">{{ unitMoney(c.unit_cost) }}</td>
                <td class="num">{{ money(c.restock_cost) }}</td>
                <td class="num">{{ t('restocking.days', { count: c.lead_time_days }) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </div>
</template>

<script>
import { ref, computed, watch, onMounted } from 'vue'
import { api } from '../api'
import { useFilters } from '../composables/useFilters'
import { useI18n } from '../composables/useI18n'
import { formatCurrency, formatCurrencyWithDecimals, convertAmount, convertToUSD } from '../utils/currency'

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency, currentLocale, translateProductName, translateWarehouse } = useI18n()
    const money = (value) => formatCurrency(value, currentCurrency.value)
    // Unit prices need cents (e.g. $9.80); totals follow the app-wide whole-unit style.
    const unitMoney = (value) => formatCurrencyWithDecimals(value, currentCurrency.value, 2)

    const { selectedLocation, selectedCategory, getCurrentFilters } = useFilters()

    const loading = ref(true)
    const error = ref(null)
    const candidates = ref([])

    // Bound to the slider/number inputs in the *display* currency (yen in ja,
    // dollars in en) so what the user types matches what the stat cards show.
    const budgetInput = ref(0)
    // Only auto-set the budget the first time candidates load; afterwards the user
    // owns the value and it should persist across filter changes (just clamped below).
    const budgetInitialized = ref(false)

    // sku -> boolean. Presence of a key means the user manually overrode the
    // server's greedy recommendation for that item.
    const overrides = ref({})

    const submitting = ref(false)
    const submitError = ref(null)
    const lastOrder = ref(null)

    // `budgetInput` is bound to the inputs with v-model.number, which yields '' (or a
    // partial string like '-') while the number box is being edited. This normalised
    // value (still in display currency) is what everything else reads so a half-typed
    // budget behaves as 0 instead of NaN.
    const budgetInputAmount = computed(() => {
      const n = Number(budgetInput.value)
      return Number.isFinite(n) && n > 0 ? n : 0
    })

    // The backend (and all selection maths below) only understands USD, regardless of
    // what currency is being displayed, so every input value is normalised back to USD
    // here before it's used for anything other than rendering the inputs themselves.
    const budgetAmount = computed(() => convertToUSD(budgetInputAmount.value, currentCurrency.value))

    const totalNeed = computed(() => candidates.value.reduce((sum, c) => sum + c.restock_cost, 0))
    // Display-currency version of the total need, used only to size the slider/step -
    // the actual budget maths above stays in USD.
    const totalNeedDisplay = computed(() => convertAmount(totalNeed.value, currentCurrency.value))

    // Round the ceiling up to a "nice" round number in the display currency. A JPY
    // total is ~150x the USD one, so flooring/step-rounding to the same 1,000/10 would
    // look arbitrarily coarse in USD or arbitrarily fine in JPY. Using "$1,000 worth"
    // in the display currency (via convertAmount, which owns the exchange rate) keeps
    // the granularity equivalent in either currency without duplicating the rate here.
    const roundingUnit = computed(() => convertAmount(1000, currentCurrency.value))
    const sliderMax = computed(() => {
      const unit = roundingUnit.value
      return Math.max(unit, Math.ceil(totalNeedDisplay.value / unit) * unit)
    })

    const sliderStep = computed(() => {
      const unit = roundingUnit.value / 100
      return Math.max(unit, Math.round(sliderMax.value / 200 / unit) * unit)
    })

    // Greedy pass over server-provided (urgency-sorted) candidates: keep taking
    // items while the budget allows. If an item doesn't fit we skip it (not break)
    // so a single expensive urgent item doesn't strand budget that a cheaper,
    // lower-urgency item further down the list could still use.
    const recommendedSkus = computed(() => {
      const result = new Set()
      // Work in integer cents to avoid floating point drift when comparing sums.
      let remainingCents = Math.round(budgetAmount.value * 100)
      for (const c of candidates.value) {
        const costCents = Math.round(c.restock_cost * 100)
        if (costCents <= remainingCents) {
          result.add(c.sku)
          remainingCents -= costCents
        }
      }
      return result
    })

    const isSelected = (c) => {
      return c.sku in overrides.value ? overrides.value[c.sku] : recommendedSkus.value.has(c.sku)
    }

    const selectedItems = computed(() => candidates.value.filter(isSelected))

    const allocated = computed(() => selectedItems.value.reduce((sum, c) => sum + c.restock_cost, 0))
    const remaining = computed(() => budgetAmount.value - allocated.value)
    // Compare in cents to avoid false positives from floating point rounding.
    const overBudget = computed(() => Math.round(allocated.value * 100) > Math.round(budgetAmount.value * 100))

    // Count only overrides that still diverge from the current recommendation.
    // After the budget moves, an earlier manual pick may coincide with what the
    // greedy pass now recommends; showing it as a "manual change" would be noise.
    const overrideCount = computed(() =>
      Object.entries(overrides.value).filter(([sku, on]) => on !== recommendedSkus.value.has(sku)).length
    )

    const canSubmit = computed(() => selectedItems.value.length > 0 && !overBudget.value && !submitting.value)

    // Default budget on first load: a quarter of the total restocking need, rounded
    // to a tidy number, so the page opens with a meaningful partial recommendation.
    // Operates on display-currency figures, same as sliderMax/budgetInput.
    const round25pct = (max) => Math.round((max * 0.25) / 10) * 10

    const loadCandidates = async () => {
      loading.value = true
      error.value = null
      try {
        candidates.value = await api.getRestockCandidates(getCurrentFilters())
        // Candidate set changed (different filters), so any manual overrides
        // no longer necessarily refer to visible rows - drop them.
        overrides.value = {}

        if (!budgetInitialized.value) {
          budgetInput.value = round25pct(sliderMax.value)
          budgetInitialized.value = true
        } else {
          // Keep the user's chosen budget across filter changes, just clamp it
          // so it never exceeds the (possibly smaller) new slider range. Both
          // sides are in display currency here.
          budgetInput.value = Math.min(budgetInputAmount.value, sliderMax.value)
        }
      } catch (err) {
        error.value = 'Failed to load restock candidates: ' + err.message
      } finally {
        loading.value = false
      }
    }

    watch([selectedLocation, selectedCategory], () => {
      loadCandidates()
    })

    // If the locale (and thus display currency) changes, convert the current input so
    // the underlying USD budget - and therefore the greedy selection - is unaffected by
    // the currency switch (modulo rounding to a whole display unit). Guarded so this
    // doesn't fire before the initial candidates load has set a real budget.
    watch(currentCurrency, (next, prev) => {
      if (!budgetInitialized.value) return
      budgetInput.value = Math.round(convertAmount(convertToUSD(budgetInputAmount.value, prev), next))
    })

    const toggle = (c) => {
      const next = !isSelected(c)
      if (next === recommendedSkus.value.has(c.sku)) {
        // Back in sync with the recommendation - no longer a manual override.
        const updated = { ...overrides.value }
        delete updated[c.sku]
        overrides.value = updated
      } else {
        overrides.value = { ...overrides.value, [c.sku]: next }
      }
    }

    const resetSelection = () => {
      overrides.value = {}
    }

    const onBudgetInput = () => {
      // Intentionally keep manual overrides while dragging the slider - only
      // filter changes reset them, so the user's picks don't get wiped mid-drag.
    }

    const formatDate = (iso) => {
      const date = new Date(iso)
      if (isNaN(date.getTime())) return iso
      const locale = currentLocale.value === 'ja' ? 'ja-JP' : 'en-US'
      return date.toLocaleDateString(locale, { year: 'numeric', month: 'short', day: 'numeric' })
    }

    const placeOrder = async () => {
      if (!canSubmit.value) return
      submitting.value = true
      submitError.value = null
      try {
        const payload = {
          budget: budgetAmount.value,
          items: selectedItems.value.map(c => ({ sku: c.sku, quantity: c.recommended_quantity }))
        }
        const response = await api.createRestockOrder(payload)
        lastOrder.value = response
        overrides.value = {}
      } catch (err) {
        const detail = err.response?.data?.detail
        if (Array.isArray(detail)) {
          submitError.value = detail.map(d => d.msg).join(', ')
        } else if (typeof detail === 'string') {
          submitError.value = detail
        } else {
          submitError.value = err.message
        }
      } finally {
        submitting.value = false
      }
    }

    onMounted(loadCandidates)

    return {
      t,
      currentCurrency,
      money,
      unitMoney,
      loading,
      error,
      candidates,
      budgetInput,
      budgetAmount,
      sliderMax,
      sliderStep,
      isSelected,
      selectedItems,
      allocated,
      remaining,
      overBudget,
      overrideCount,
      canSubmit,
      submitting,
      submitError,
      lastOrder,
      toggle,
      resetSelection,
      onBudgetInput,
      placeOrder,
      formatDate,
      translateProductName,
      translateWarehouse
    }
  }
}
</script>

<style scoped>
.success-banner {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  color: #166534;
  border-radius: 8px;
  padding: 1rem 1.25rem;
  margin-bottom: 1.5rem;
}

.success-banner .small {
  font-size: 0.813rem;
  margin-top: 0.25rem;
}

.banner-actions {
  display: flex;
  gap: 0.75rem;
  flex-shrink: 0;
}

.budget-card {
  margin-bottom: 1.5rem;
}

.budget-value {
  font-size: 1.25rem;
  font-weight: 700;
  color: #0f172a;
}

.budget-controls {
  display: grid;
  grid-template-columns: 1fr 160px;
  gap: 1rem;
  align-items: center;
}

.budget-slider {
  width: 100%;
  height: 6px;
  accent-color: #3b82f6;
}

.budget-input {
  width: 100%;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 0.5rem 0.75rem;
  font-size: 0.875rem;
  color: #0f172a;
}

.help-text {
  color: #64748b;
  font-size: 0.8125rem;
  margin-top: 0.75rem;
}

.card-subtitle {
  color: #64748b;
  font-size: 0.8125rem;
  margin-top: 0.25rem;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.manual-hint {
  color: #64748b;
  font-size: 0.8125rem;
}

.warning-text {
  color: #b45309;
  font-size: 0.8125rem;
  margin: 0.75rem 1.25rem 0;
}

.muted-text {
  color: #64748b;
  font-size: 0.8125rem;
  margin: 0.75rem 1.25rem 0;
}

.empty-state {
  text-align: center;
  color: #64748b;
  padding: 3rem;
}

.btn-primary {
  background: #3b82f6;
  color: white;
  border: none;
  padding: 0.5rem 1rem;
  border-radius: 6px;
  font-weight: 500;
  font-size: 0.875rem;
  cursor: pointer;
  white-space: nowrap;
}

.btn-primary:hover:not(:disabled) {
  background: #2563eb;
}

.btn-primary:disabled {
  background: #cbd5e1;
  cursor: not-allowed;
}

.btn-secondary {
  background: white;
  border: 1px solid #e2e8f0;
  color: #475569;
  padding: 0.5rem 1rem;
  border-radius: 6px;
  font-weight: 500;
  font-size: 0.875rem;
  cursor: pointer;
  text-decoration: none;
  display: inline-block;
  white-space: nowrap;
}

.btn-secondary:hover {
  background: #f8fafc;
}

.col-include {
  width: 72px;
}

.restock-table tbody tr {
  cursor: pointer;
}

.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.row-muted td {
  color: #94a3b8;
}

.row-muted .badge {
  opacity: 0.55;
}
</style>
