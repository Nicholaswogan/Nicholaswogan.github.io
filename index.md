---
title: Home
---

<div>
    <img src="{{ '/Kbasin.jpg' | absolute_url }}" alt="jekyll icon" style="width:45%;" >
</div>

# Learning things
<div class="toc" markdown="1">
## Contents:

{% for lesson in site.pages %}
{% if lesson.nav == true %}- [{{ lesson.title }}]({{ lesson.url | absolute_url }}){% endif %}
{% endfor %}
</div>
