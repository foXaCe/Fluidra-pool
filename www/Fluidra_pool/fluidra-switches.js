// Fluidra Pool Switch Enhancements
// Améliore l'expérience utilisateur avec des feedbacks visuels immédiats

class FluidraSwitchEnhancer {
  constructor() {
    this.observers = new Map();
    this.pendingStates = new Map();
    this.init();
  }

  init() {
    // Observer les changements DOM pour détecter les nouveaux switches
    this.observeDOM();

    // Démarrer les améliorations pour les switches existants
    this.enhanceExistingSwitches();

    // Écouter les événements personnalisés de Home Assistant
    this.listenToHAEvents();
  }

  observeDOM() {
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        if (mutation.type === 'childList') {
          mutation.addedNodes.forEach((node) => {
            if (node.nodeType === Node.ELEMENT_NODE) {
              this.enhanceSwitchesInElement(node);
            }
          });
        }
      });
    });

    observer.observe(document.body, {
      childList: true,
      subtree: true
    });
  }

  enhanceExistingSwitches() {
    this.enhanceSwitchesInElement(document);
  }

  enhanceSwitchesInElement(element) {
    // Trouver tous les switches Fluidra
    const fluidraSwitches = element.querySelectorAll(
      'ha-entity-toggle[data-entity-id*="fluidra"], ' +
      'ha-entity-toggle[data-entity-id*="pompe"], ' +
      'ha-entity-toggle[data-entity-id*="auto"], ' +
      'ha-entity-toggle[data-entity-id*="schedule"]'
    );

    fluidraSwitches.forEach(switchElement => {
      this.enhanceSwitch(switchElement);
    });
  }

  enhanceSwitch(switchElement) {
    if (switchElement.hasAttribute('data-fluidra-enhanced')) {
      return; // Déjà amélioré
    }

    switchElement.setAttribute('data-fluidra-enhanced', 'true');

    // Ajouter des écouteurs d'événements
    switchElement.addEventListener('click', (event) => {
      this.handleSwitchClick(switchElement, event);
    });

    // Observer les changements d'état
    this.observeStateChanges(switchElement);
  }

  handleSwitchClick(switchElement, event) {
    const entityId = switchElement.getAttribute('data-entity-id');

    // Ajouter l'état en attente immédiatement
    this.setPendingState(switchElement, true);

    // Ajouter des classes CSS pour le feedback visuel
    switchElement.classList.add('fluidra-pending');
    switchElement.setAttribute('data-pending', 'true');

    // Programmer un timeout de sécurité
    setTimeout(() => {
      this.clearPendingState(switchElement);
    }, 10000); // 10 secondes de timeout

    console.log(`🔄 Fluidra switch clicked: ${entityId}`);
  }

  setPendingState(switchElement, isPending) {
    const entityId = switchElement.getAttribute('data-entity-id');

    if (isPending) {
      this.pendingStates.set(entityId, Date.now());
      switchElement.setAttribute('data-pending', 'true');
      switchElement.classList.add('fluidra-pending');

      // Ajouter un indicateur visuel
      this.addLoadingIndicator(switchElement);
    } else {
      this.clearPendingState(switchElement);
    }
  }

  clearPendingState(switchElement) {
    const entityId = switchElement.getAttribute('data-entity-id');

    this.pendingStates.delete(entityId);
    switchElement.removeAttribute('data-pending');
    switchElement.classList.remove('fluidra-pending');

    // Retirer l'indicateur de chargement
    this.removeLoadingIndicator(switchElement);
  }

  addLoadingIndicator(switchElement) {
    // Retirer l'ancien indicateur s'il existe
    this.removeLoadingIndicator(switchElement);

    const indicator = document.createElement('div');
    indicator.className = 'fluidra-loading-indicator';
    indicator.innerHTML = `
      <svg class="fluidra-spinner" viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2" fill="none" stroke-dasharray="60" stroke-dashoffset="60" stroke-linecap="round">
          <animate attributeName="stroke-dashoffset" dur="1.5s" values="60;0;60" repeatCount="indefinite"/>
        </circle>
      </svg>
    `;

    switchElement.appendChild(indicator);
  }

  removeLoadingIndicator(switchElement) {
    const indicator = switchElement.querySelector('.fluidra-loading-indicator');
    if (indicator) {
      indicator.remove();
    }
  }

  observeStateChanges(switchElement) {
    const entityId = switchElement.getAttribute('data-entity-id');

    // Observer les changements d'attributs
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        if (mutation.type === 'attributes' && mutation.attributeName === 'aria-checked') {
          // L'état a changé - effacer l'état en attente
          if (this.pendingStates.has(entityId)) {
            setTimeout(() => {
              this.clearPendingState(switchElement);
            }, 500); // Petit délai pour l'animation
          }
        }
      });
    });

    observer.observe(switchElement, {
      attributes: true,
      attributeFilter: ['aria-checked']
    });

    this.observers.set(entityId, observer);
  }

  listenToHAEvents() {
    // Écouter les événements de changement d'état de Home Assistant
    document.addEventListener('hass-more-info', (event) => {
      // Rafraîchir les améliorations quand une popup more-info s'ouvre
      setTimeout(() => {
        this.enhanceExistingSwitches();
      }, 100);
    });

    // Écouter les changements de vue
    window.addEventListener('location-changed', () => {
      setTimeout(() => {
        this.enhanceExistingSwitches();
      }, 500);
    });
  }

  // Méthode publique pour forcer le rafraîchissement
  refresh() {
    this.enhanceExistingSwitches();
  }

  // Nettoyer les observateurs
  destroy() {
    this.observers.forEach((observer, entityId) => {
      observer.disconnect();
    });
    this.observers.clear();
    this.pendingStates.clear();
  }
}

// CSS dynamique pour les indicateurs de chargement
const dynamicStyles = `
.fluidra-loading-indicator {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  pointer-events: none;
  z-index: 10;
}

.fluidra-spinner {
  width: 16px;
  height: 16px;
  color: var(--primary-color);
}

.fluidra-pending {
  opacity: 0.7;
  transition: opacity 0.3s ease;
}

.fluidra-pending:hover {
  opacity: 0.9;
}

/* États spécifiques par type de switch */
ha-entity-toggle[data-entity-id*="pompe"].fluidra-pending {
  --switch-checked-color: #1976D2;
  --switch-unchecked-color: #1976D2;
}

ha-entity-toggle[data-entity-id*="auto"].fluidra-pending {
  --switch-checked-color: #388E3C;
  --switch-unchecked-color: #388E3C;
}

ha-entity-toggle[data-entity-id*="schedule"].fluidra-pending {
  --switch-checked-color: #F57C00;
  --switch-unchecked-color: #F57C00;
}
`;

// Injecter les styles dynamiques
function injectStyles() {
  const styleElement = document.createElement('style');
  styleElement.textContent = dynamicStyles;
  document.head.appendChild(styleElement);
}

// Initialiser quand le DOM est prêt
function initFluidraSwitchEnhancer() {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      injectStyles();
      window.fluidraSwitchEnhancer = new FluidraSwitchEnhancer();
    });
  } else {
    injectStyles();
    window.fluidraSwitchEnhancer = new FluidraSwitchEnhancer();
  }
}

// Démarrer l'amélioration
initFluidraSwitchEnhancer();

// Exporter pour usage global
window.FluidraSwitchEnhancer = FluidraSwitchEnhancer;