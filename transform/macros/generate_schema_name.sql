{# Name schemas `staging`, `intermediate` and `marts`, not dbt's default `main_staging` etc. #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name or target.schema }}
{%- endmacro %}
