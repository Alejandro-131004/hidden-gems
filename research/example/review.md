---

title: "Demo Dashboard 2 Code Review"
author: "Rafael Andre"
date: "2026-08-24"
documentclass: article
geometry: margin=1in
output: pdf_document

---

# Novas Implementacoes

### 3. New files added

Strength score? Como é que isso é determinado? Como é que é usado?
 1. *a hand-set assumption, not derived from data* nao e aceitavel em production, sao so valores aleatorios e percentagens tiradas do nada, coeficientes da UEFA sao reais mas a atribuicao da importancia esta a ser completamente aleatoria
 2. *no UEFA coefficient exists: uses that league's median `Market value`* mistura criterios, faz com que sejam incomparaveis e inconsistentes e por isso nao se pode usar
 3. nao percebi be o proposito de devkit/concept_analysis.py


### 5. How a score gets built

É um processo demasiado denso, com demasiadas assumptions. Parece inconsitente e difícil de acompanhar.

Outra coisa: nao contar a idade nao faz sentido, no maximo retiramos do metodo de calculo de market value mas incluimos um filtro no dashboard. E natural que jogadores com mais experiencia exibam mais performance, mas tambem e preciso considerar que para purchase-resell value a idade conta muito, e em especial para potencial.

#### 5.1 Two score columns

Gostei da ideia de duas colunas, uma com performance relativa ao interior da propria liga e outra mais global.


# Resolucoes aos TODO

 1. 6.1 esta errada, o objetivo era sim market value approximation
 2. O ponto *"Neither of these changes explains why the overall correlation is still weak. The main issue is that `Market value` isn’t a reliable ground truth for this comparison, for three separate reasons that we calculate below."* nao faz sentido, o target e market value e deve ser possivel aproxima-lo com as features que temos. 
    1. A minha ideia para resolver isto e remover todos os que tem value 0 de forma a usar AI ou PCA-tSNE para identificacao de feature importance, importancia essa que podemos usar para definir os pesos de liga e tambem skills
 3. 6.2 e uma pseudo solucao, acho melhor usarmos o que sugeri em 2.1.
 4. 6.3 e algo a resolver depois da reuniao, nao podemos simplesmente assumir percentagens e pesos com numeros extremamente reduzidos de features, iamos ter de fazer um modelo por cada role ou entao ficar com uma ensemble de modelos que ia cair na underrepresentation na mesma
 5. *Not the old `demo_dashboard1` index that section 6 just used for diagnostics, that one only exists to check old claims, nobody uses it to actually rank anyone anymore.* nao e isso que o meu indice faz, e nao entendo porque e que nao e usado?
 6. *We don't actually know if the filter works, because we have never tested it.*   ?????!!  *Checked, full stop, never happened.* e uma conclusao errada, nao e isso que o filtro faz

# Open Questions

 1. goal esta errado
 2. classifica metricas como redundantes, nao o sao, simplesmente sao complementares e ainda nao temos uma maneira de as agregar corretamente no filtro, a AI vai resolver isso
 3. respondido acima
 4. strength value nao pode ser assim definido
 5. market value e o target por enquanto
 6. *Is this notebook meant for the company, or for us?* para nos, mas convem deixar production ready para podermos usar como projeto desenvolvido no curriculo



